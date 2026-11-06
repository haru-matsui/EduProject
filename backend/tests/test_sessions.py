from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import BrowserSession
from .helpers import API, project_body


def test_session_is_server_side_private_and_revocable(env):
    client, user = env.identity("Name")
    response = client.get(API + "/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {"id": str(user.id), "display_name": "Name", "group_name": None, "timezone": "UTC"}
    assert len(body["csrf_token"]) >= 32
    assert "telegram" not in str(body["user"])
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    raw = client.cookies.get(env.settings.session_cookie)
    with env.app.state.session_factory() as db:
        stored = db.scalar(select(BrowserSession))
        assert raw != stored.token_hash and len(stored.token_hash) == 64
    assert client.post(API + "/auth/logout").status_code == 204
    client.cookies.set(env.settings.session_cookie, raw)
    assert client.get(API + "/auth/session").status_code == 401
    assert client.post(API + "/auth/logout").status_code == 401


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.invalid"}, {"Origin": "null"}, {"X-CSRF-Token": "wrong"}, {"Sec-Fetch-Site": "cross-site"}])
def test_csrf_and_origin_rejections_do_not_mutate(env, headers):
    client, _ = env.identity()
    response = client.post(API + "/projects", json=project_body(), headers=headers)
    assert response.status_code == 403
    assert client.get(API + "/projects").json()["total"] == 0


def test_missing_origin_and_token_are_rejected(env):
    client, _ = env.identity()
    del client.headers["Origin"]
    assert client.post(API + "/projects", json=project_body()).status_code == 403
    client.headers["Origin"] = "https://testserver"
    del client.headers["X-CSRF-Token"]
    assert client.post(API + "/projects", json=project_body()).status_code == 403


def test_cross_origin_session_read_denied(env):
    client, _ = env.identity()
    assert client.get(API + "/auth/session", headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.get(API + "/auth/session", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403


def test_expired_and_forged_session_not_accepted(env):
    client, _ = env.identity()
    with env.app.state.session_factory() as db:
        db.scalar(select(BrowserSession)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(API + "/auth/session").status_code == 401
    client.cookies.set(env.settings.session_cookie, "fake" * 20)
    assert client.get(API + "/projects").status_code == 401


def test_anonymous_no_bypass_or_client_selected_identity(env):
    client = env.client()
    assert client.get(API + "/projects").status_code == 401
    assert client.post(API + "/projects", json=project_body()).status_code == 401
    assert client.get(API + "/auth/dev-login?telegram_id=123&role=lead").status_code == 404
    assert client.post(API + "/auth/login", json={"telegram_id": 123}).status_code == 404
    assert client.get(API + "/auth/session", headers={"X-User-ID": "123"}).status_code == 401


def test_unconfigured_auth_clear_status_and_health(env):
    client = env.client()
    assert client.get(API + "/auth/configuration").json() == {"telegram_login_configured": False}
    response = client.get(API + "/auth/telegram/start")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
