import base64
import hashlib
import re
import secrets

import httpx
import jwt

ISSUER = "https://oauth.telegram.org"
AUTHORIZE_URL = ISSUER + "/auth"
TOKEN_URL = ISSUER + "/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


class OIDCFailure(Exception):
    """Only fixed diagnostic labels may leave the provider boundary."""
    REASONS = frozenset({
        "provider_validation_failed", "invalid_id_token", "unsupported_token_header",
        "signing_key_not_found", "invalid_subject", "nonce_mismatch",
        "authorized_party_mismatch", "invalid_telegram_id", "missing_nonce",
        "invalid_signature", "invalid_audience", "invalid_issuer", "expired_token",
        "invalid_token_claims", "invalid_provider_response",
        "token_client_rejected", "token_grant_rejected", "token_request_rejected", "provider_token_error",
        "token_request_timeout", "token_request_network_error", "token_request_http_error",
        "jwks_request_timeout", "jwks_request_network_error", "jwks_request_http_error",
    })

    def __init__(self, reason="provider_validation_failed", http_status=None):
        self.reason = reason if reason in self.REASONS else "provider_validation_failed"
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(self.reason)


class TelegramOIDC:
    """Authorization Code + S256 PKCE, official Telegram endpoints only.

    See https://core.telegram.org/bots/telegram-login (verified 2026-10-28).
    No userinfo request and no trust in browser-supplied Telegram identities.
    """
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def exchange_and_validate(self, code: str, verifier: str, nonce: str) -> dict:
        stage = "token_request"
        try:
            with httpx.Client(timeout=10, follow_redirects=False, transport=self.transport) as client:
                response = client.post(TOKEN_URL, auth=(self.settings.telegram_client_id, self.settings.telegram_client_secret.get_secret_value()), data={
                    "grant_type": "authorization_code", "code": code,
                    "redirect_uri": self.settings.telegram_redirect_uri,
                    "client_id": self.settings.telegram_client_id, "code_verifier": verifier,
                })
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise OIDCFailure("invalid_provider_response")
                provider_error = payload.get("error")
                if provider_error is not None:
                    reasons = {"invalid_client": "token_client_rejected", "invalid_grant": "token_grant_rejected",
                               "invalid_request": "token_request_rejected"}
                    reason = reasons.get(provider_error, "provider_token_error") if isinstance(provider_error, str) else "provider_token_error"
                    raise OIDCFailure(reason, response.status_code)
                encoded = payload.get("id_token")
                if not isinstance(encoded, str) or len(encoded) > 20000:
                    raise OIDCFailure("invalid_id_token")
                header = jwt.get_unverified_header(encoded)
                if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                    raise OIDCFailure("unsupported_token_header")
                stage = "jwks_request"
                keys_response = client.get(JWKS_URL)
                keys_response.raise_for_status()
                keys = keys_response.json().get("keys", [])
                matches = [key for key in keys if key.get("kid") == header["kid"] and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"]
                if len(matches) != 1:
                    raise OIDCFailure("signing_key_not_found")
                stage = "token_validation"
                key = jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key
                claims = jwt.decode(encoded, key, algorithms=["RS256"], audience=self.settings.telegram_client_id, issuer=ISSUER,
                                    options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]}, leeway=30)
                subject = claims["sub"]
                if not isinstance(subject, str) or not 1 <= len(subject) <= 256:
                    raise OIDCFailure("invalid_subject")
                if not isinstance(claims["nonce"], str) or not secrets.compare_digest(claims["nonce"], nonce):
                    raise OIDCFailure("nonce_mismatch")
                if "azp" in claims and str(claims["azp"]) != self.settings.telegram_client_id:
                    raise OIDCFailure("authorized_party_mismatch")
                if isinstance(claims.get("aud"), list) and len(claims["aud"]) > 1 and claims.get("azp") != self.settings.telegram_client_id:
                    raise OIDCFailure("authorized_party_mismatch")
                telegram_id = claims.get("id")
                # Normalize decimal profile IDs before persisting them in BIGINT.
                if isinstance(telegram_id, str) and re.fullmatch(r"[1-9][0-9]{0,18}", telegram_id):
                    telegram_id = int(telegram_id)
                if telegram_id is not None and (isinstance(telegram_id, bool) or not isinstance(telegram_id, int) or not 0 < telegram_id < 2**63):
                    raise OIDCFailure("invalid_telegram_id")
                if telegram_id is not None:
                    claims["id"] = telegram_id
                return claims
        except httpx.TimeoutException:
            raise OIDCFailure(stage + "_timeout") from None
        except httpx.HTTPStatusError as exc:
            raise OIDCFailure(stage + "_http_error", exc.response.status_code) from None
        except httpx.HTTPError:
            raise OIDCFailure(stage + "_network_error") from None
        except jwt.MissingRequiredClaimError as exc:
            raise OIDCFailure("missing_nonce" if exc.claim == "nonce" else "invalid_token_claims") from None
        except jwt.InvalidSignatureError:
            raise OIDCFailure("invalid_signature") from None
        except jwt.InvalidAudienceError:
            raise OIDCFailure("invalid_audience") from None
        except jwt.InvalidIssuerError:
            raise OIDCFailure("invalid_issuer") from None
        except jwt.ExpiredSignatureError:
            raise OIDCFailure("expired_token") from None
        except jwt.PyJWTError:
            raise OIDCFailure("invalid_token_claims") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            # Never propagate provider bodies, tokens, codes or credentials.
            raise OIDCFailure("invalid_provider_response") from None
