import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from sqlalchemy import func, select

from app.models import BrowserSession, OIDCFlow, User
from app.oidc import AUTHORIZE_URL, ISSUER, JWKS_URL, TOKEN_URL, TelegramOIDC, pkce_challenge
from .helpers import API


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Provider:
    def __init__(self, env, key, mutate=None, wrong_key=None):
        self.env, self.key, self.mutate, self.wrong_key = env, key, mutate, wrong_key
        self.authorization = None
        self.exchanges = 0

    def handler(self, request):
        if str(request.url) == TOKEN_URL:
            self.exchanges += 1
            assert request.headers["authorization"] == "Basic " + base64.b64encode(b"test-client:secret-for-tests-only").decode()
            fields = parse_qs(request.content.decode())
            assert fields["grant_type"] == ["authorization_code"]
            assert fields["redirect_uri"] == [self.env.settings.telegram_redirect_uri]
            assert fields["client_id"] == ["test-client"]
            assert pkce_challenge(fields["code_verifier"][0]) == self.authorization["code_challenge"][0]
            stamp = datetime.now(UTC)
            claims = {"iss": ISSUER, "aud": "test-client", "sub": "verified-sub", "id": 12345678, "name": "Telegram User", "iat": stamp, "exp": stamp + timedelta(minutes=5), "nonce": self.authorization["nonce"][0]}
            if self.mutate:
                self.mutate(claims)
            encoded = jwt.encode(claims, self.wrong_key or self.key, algorithm="RS256", headers={"kid": "fixture-key"})
            return httpx.Response(200, json={"id_token": encoded})
        if str(request.url) == JWKS_URL:
            key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [{**key, "kid": "fixture-key", "alg": "RS256", "use": "sig"}]})
        raise AssertionError("Unexpected provider endpoint")

    def start(self, client, return_path="/projects"):
        self.env.settings.telegram_client_id = "test-client"
        self.env.settings.telegram_client_secret = SecretStr("secret-for-tests-only")
        self.env.app.state.oidc = TelegramOIDC(self.env.settings, httpx.MockTransport(self.handler))
        response = client.get(API + "/auth/telegram/start", params={"return_path": return_path})
        assert response.status_code == 302
        assert response.headers["location"].startswith(AUTHORIZE_URL + "?")
        self.authorization = parse_qs(urlsplit(response.headers["location"]).query)
        assert self.authorization["code_challenge_method"] == ["S256"]
        assert self.authorization["scope"] == ["openid profile"]
        assert self.authorization["response_type"] == ["code"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "SameSite=lax" in response.headers["set-cookie"]
        assert "Domain=" not in response.headers["set-cookie"]
        return self.authorization["state"][0]


def test_real_signature_jwks_pkce_session_rotation_and_user_upsert(env, signing_key):
    provider = Provider(env, signing_key)
    client = env.client()
    state = provider.start(client)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "authorized-code"})
    assert response.status_code == 303 and response.headers["location"] == "/projects"
    assert "__Host-eduproject_session=" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"] and "Secure" in response.headers["set-cookie"]
    assert client.get(API + "/auth/session").json()["user"]["display_name"] == "Telegram User"
    old_session = client.cookies.get(env.settings.session_cookie)
    state = provider.start(client)
    assert client.get(API + "/auth/telegram/callback", params={"state": state, "code": "second-code"}).status_code == 303
    assert client.cookies.get(env.settings.session_cookie) != old_session
    with env.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        sessions = db.scalars(select(BrowserSession)).all()
        assert len(sessions) == 2 and sum(row.revoked_at is None for row in sessions) == 1


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(iss="https://evil.invalid"),
    lambda c: c.update(aud="another-client"),
    lambda c: c.update(exp=datetime.now(UTC) - timedelta(minutes=5)),
    lambda c: c.update(iat=datetime.now(UTC) + timedelta(hours=1)),
    lambda c: c.update(nonce="different-nonce"),
    lambda c: c.pop("nonce"),
    lambda c: c.update(azp="another-client"),
    lambda c: c.update(id=True),
])
def test_invalid_claims_never_mint_session(env, signing_key, mutate):
    provider = Provider(env, signing_key, mutate)
    client = env.client()
    state = provider.start(client)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"})
    assert response.status_code == 303 and response.headers["location"] == "/login?error=authorization_failed"
    assert client.get(API + "/auth/session").status_code == 401
    with env.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(BrowserSession)) == 0


def test_wrong_signature_rejected(env, signing_key):
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = Provider(env, signing_key, wrong_key=wrong)
    client = env.client()
    state = provider.start(client)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"})
    assert "authorization_failed" in response.headers["location"]
    assert client.get(API + "/auth/session").status_code == 401


def test_browser_binding_state_expiry_cancel_and_single_use(env, signing_key):
    provider = Provider(env, signing_key)
    client = env.client()
    state = provider.start(client)
    binding = client.cookies.get(env.settings.flow_cookie)
    attacker = env.client()
    assert "authorization_failed" in attacker.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"}).headers["location"]
    assert provider.exchanges == 0
    assert "authorization_failed" in client.get(API + "/auth/telegram/callback", params={"state": "wrong" * 8, "code": "code"}).headers["location"]
    client.cookies.set(env.settings.flow_cookie, binding)
    assert client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"}).headers["location"] == "/projects"
    client.cookies.set(env.settings.flow_cookie, binding)
    assert "authorization_failed" in client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"}).headers["location"]
    assert provider.exchanges == 1
    state = provider.start(client)
    with env.app.state.session_factory() as db:
        flow = db.scalar(select(OIDCFlow).where(OIDCFlow.consumed_at.is_(None)))
        flow.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert "authorization_failed" in client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"}).headers["location"]
    state = provider.start(client)
    assert "authorization_failed" in client.get(API + "/auth/telegram/callback", params={"state": state, "error": "access_denied"}).headers["location"]
    assert provider.exchanges == 1


@pytest.mark.parametrize("path", ["https://evil.invalid", "//evil.invalid", "/\\evil.invalid", "/projects?redirect=https://evil.invalid", "/projects/../../evil", "/login", "/invitations/token?x=1"])
def test_no_open_redirect(env, signing_key, path):
    env.settings.telegram_client_id = "test-client"
    env.settings.telegram_client_secret = SecretStr("secret-for-tests-only")
    response = env.client().get(API + "/auth/telegram/start", params={"return_path": path})
    assert response.status_code == 400


def test_invitation_return_path_kept_server_side_and_validation_hides_secrets(env, signing_key):
    provider = Provider(env, signing_key)
    client = env.client()
    route = "/invitations/" + "A" * 43
    state = provider.start(client, route)
    assert route not in str(provider.authorization)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "code"})
    assert response.headers["location"] == route
    secret_code = "VERY_SECRET_CODE" * 200
    rejected = client.get(API + "/auth/telegram/callback", params={"state": state, "code": secret_code})
    assert rejected.status_code == 422 and "VERY_SECRET_CODE" not in rejected.text


def test_missing_nonce_logs_safe_reason_without_creating_session(env, signing_key, caplog):
    provider = Provider(env, signing_key, mutate=lambda claims: claims.pop("nonce"))
    client = env.client()
    state = provider.start(client)
    nonce = provider.authorization["nonce"][0]
    secret_code = "callback-code-must-not-be-logged"
    with caplog.at_level("WARNING", logger="app.auth"):
        response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": secret_code})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=authorization_failed"
    assert "reason=missing_nonce" in caplog.text
    assert all(value not in caplog.text for value in (state, nonce, secret_code, "secret-for-tests-only"))
    assert client.get(API + "/auth/session").status_code == 401


@pytest.mark.parametrize(("failure", "expected_reason", "http_status"), [
    ("http", "token_request_http_error", 400),
    ("timeout", "token_request_timeout", None),
    ("network", "token_request_network_error", None),
])
def test_provider_failures_log_only_safe_diagnostics(env, signing_key, monkeypatch, caplog, failure, expected_reason, http_status):
    provider = Provider(env, signing_key)
    private_detail = "UPSTREAM_BODY_TOKEN_AND_CREDENTIAL_CANARY"

    def failed_provider(request):
        assert str(request.url) == TOKEN_URL
        if failure == "http":
            return httpx.Response(400, json={"error": private_detail, "error_description": private_detail})
        if failure == "timeout":
            raise httpx.ReadTimeout(private_detail, request=request)
        raise httpx.ConnectError(private_detail, request=request)

    monkeypatch.setattr(provider, "handler", failed_provider)
    client = env.client()
    state = provider.start(client)
    nonce = provider.authorization["nonce"][0]
    secret_code = "callback-code-must-not-be-logged"
    with caplog.at_level("WARNING", logger="app.auth"):
        response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": secret_code})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=authorization_failed"
    assert f"reason={expected_reason}" in caplog.text
    assert f"http_status={http_status}" in caplog.text
    for value in (private_detail, state, nonce, secret_code, "secret-for-tests-only"):
        assert value not in caplog.text
        assert value not in response.text
        assert value not in response.headers["location"]
    assert client.get(API + "/auth/session").status_code == 401


def test_missing_browser_binding_logs_reason_before_provider_exchange(env, signing_key, caplog):
    provider = Provider(env, signing_key)
    state = provider.start(env.client())
    unbound_browser = env.client()
    secret_code = "unbound-callback-code-must-not-be-logged"
    with caplog.at_level("WARNING", logger="app.auth"):
        response = unbound_browser.get(API + "/auth/telegram/callback", params={"state": state, "code": secret_code})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=authorization_failed"
    assert "reason=invalid_callback_binding" in caplog.text
    assert state not in caplog.text and secret_code not in caplog.text
    assert provider.exchanges == 0
    assert unbound_browser.get(API + "/auth/session").status_code == 401


@pytest.mark.parametrize(("oauth_error", "expected_reason"), [
    ("invalid_client", "token_client_rejected"),
    ("invalid_grant", "token_grant_rejected"),
    ("invalid_request", "token_request_rejected"),
    ("UNKNOWN_OAUTH_ERROR_CANARY\nreason=forged_log_entry", "provider_token_error"),
    ({"private": "NESTED_OAUTH_ERROR_CANARY"}, "provider_token_error"),
])
def test_http_200_oauth_errors_are_classified_without_logging_payload(env, signing_key, monkeypatch, caplog, oauth_error, expected_reason):
    provider = Provider(env, signing_key)
    private_detail = "OAUTH_ERROR_BODY_AND_ACCESS_TOKEN_CANARY"
    observed_urls = []

    def rejected_token(request):
        observed_urls.append(str(request.url))
        assert str(request.url) == TOKEN_URL
        return httpx.Response(200, json={
            "error": oauth_error,
            "error_description": private_detail,
            "access_token": private_detail,
        })

    monkeypatch.setattr(provider, "handler", rejected_token)
    client = env.client()
    state = provider.start(client)
    nonce = provider.authorization["nonce"][0]
    secret_code = "http-200-oauth-callback-code-canary"
    with caplog.at_level("WARNING", logger="app.auth"):
        response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": secret_code})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=authorization_failed"
    assert f"reason={expected_reason}" in caplog.text
    assert "http_status=200" in caplog.text
    assert observed_urls == [TOKEN_URL]
    for value in (private_detail, state, nonce, secret_code, "secret-for-tests-only", "UNKNOWN_OAUTH_ERROR_CANARY", "NESTED_OAUTH_ERROR_CANARY", "forged_log_entry"):
        assert value not in caplog.text
        assert value not in response.text
        assert value not in response.headers["location"]
    assert client.get(API + "/auth/session").status_code == 401


@pytest.mark.parametrize(("telegram_id", "expected_id"), [
    ("12345678", 12345678),
    (str(2**63 - 1), 2**63 - 1),
    (2**63 - 1, 2**63 - 1),
])
def test_decimal_telegram_id_is_normalized_before_persisting_user(env, signing_key, telegram_id, expected_id):
    provider = Provider(env, signing_key, mutate=lambda claims: claims.update(id=telegram_id))
    client = env.client()
    state = provider.start(client)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "authorized-code"})
    assert response.status_code == 303
    assert response.headers["location"] == "/projects"
    assert client.get(API + "/auth/session").status_code == 200
    with env.app.state.session_factory() as db:
        user = db.scalar(select(User))
        assert user.telegram_user_id == expected_id
        assert type(user.telegram_user_id) is int
        assert user.telegram_sub == "verified-sub"


@pytest.mark.parametrize("include_null_id", [False, True])
def test_optional_telegram_id_can_remain_absent(env, signing_key, include_null_id):
    def omit_id(claims):
        if include_null_id:
            claims["id"] = None
        else:
            claims.pop("id")

    provider = Provider(env, signing_key, mutate=omit_id)
    client = env.client()
    state = provider.start(client)
    response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "authorized-code"})
    assert response.status_code == 303
    assert response.headers["location"] == "/projects"
    with env.app.state.session_factory() as db:
        user = db.scalar(select(User))
        assert user.telegram_user_id is None
        assert user.telegram_sub == "verified-sub"


@pytest.mark.parametrize("telegram_id", [
    0, "0", -1, "-1", 2**63, str(2**63),
    True, False, 123.0, "123.0", "1e3", "\u0661\u0662\u0663", "\uff11\uff12\uff13",
    "9" * 200, " 123", "123 ", "+123", "0123", "",
])
def test_invalid_telegram_id_representation_never_creates_identity(env, signing_key, caplog, telegram_id):
    provider = Provider(env, signing_key, mutate=lambda claims: claims.update(id=telegram_id))
    client = env.client()
    state = provider.start(client)
    with caplog.at_level("WARNING", logger="app.auth"):
        response = client.get(API + "/auth/telegram/callback", params={"state": state, "code": "authorized-code"})
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=authorization_failed"
    assert "reason=invalid_telegram_id" in caplog.text
    assert client.get(API + "/auth/session").status_code == 401
    with env.app.state.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(BrowserSession)) == 0
