import logging
import re
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import get_db
from .errors import APIError
from .models import BrowserSession, OIDCFlow, User, now
from .oidc import AUTHORIZE_URL, ISSUER, OIDCFailure, pkce_challenge
from .schemas import SessionView, UserView
from .security import AuthContext, check_same_origin, current_auth, issue_session, random_token, token_hash, write_auth

router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)


def validate_return_path(path: str) -> str:
    if path == "/projects":
        return path
    if re.fullmatch(r"/projects/[0-9a-fA-F-]{36}", path):
        try:
            UUID(path.rsplit("/", 1)[1])
            return path
        except ValueError:
            pass
    if re.fullmatch(r"/invitations/[A-Za-z0-9_-]{20,256}", path):
        return path
    raise APIError(400, "invalid_return_path", "Недопустимый маршрут возврата.")


def set_cookie(response: Response, settings, name: str, value: str, max_age: int):
    response.set_cookie(name, value, max_age=max_age, secure=settings.cookie_secure, httponly=True, samesite="lax", path="/")


@router.get("/auth/telegram/start", status_code=302)
def start(request: Request, return_path: str = Query(default="/projects", max_length=400), db: Session = Depends(get_db)):
    settings = request.app.state.settings
    path = validate_return_path(return_path)
    if not settings.telegram_configured:
        raise APIError(503, "auth_not_configured", "Вход через Telegram ещё не настроен. Администратору нужны Client ID и Client Secret из BotFather.")
    state, browser, verifier, nonce = (random_token() for _ in range(4))
    flow = OIDCFlow(state_hash=token_hash(state), browser_hash=token_hash(browser), verifier=verifier, nonce=nonce, return_path=path, expires_at=now() + timedelta(minutes=10))
    db.add(flow)
    db.commit()
    response = RedirectResponse(AUTHORIZE_URL + "?" + urlencode({
        "client_id": settings.telegram_client_id, "redirect_uri": settings.telegram_redirect_uri,
        "response_type": "code", "scope": "openid profile", "state": state,
        "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256", "nonce": nonce,
    }), status_code=302)
    set_cookie(response, settings, settings.flow_cookie, browser, 600)
    return response


@router.get("/auth/telegram/callback", status_code=303)
def callback(request: Request, state: str = Query(default="", max_length=256), code: str | None = Query(default=None, max_length=2000), error: str | None = Query(default=None, max_length=200), db: Session = Depends(get_db)):
    settings = request.app.state.settings

    def fail(reason, http_status=None):
        # Fixed labels only: never log callback parameters or provider payloads.
        logger.warning("Telegram login rejected: reason=%s http_status=%s", reason, http_status)
        response = RedirectResponse("/login?error=authorization_failed", status_code=303)
        response.delete_cookie(settings.flow_cookie, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
        return response

    binding = request.cookies.get(settings.flow_cookie, "")
    if not settings.telegram_configured:
        return fail("auth_not_configured")
    if len(state) < 20 or not 32 <= len(binding) <= 256:
        return fail("invalid_callback_binding")
    # Atomic claim prevents two workers from exchanging the same callback.
    flow = db.scalar(select(OIDCFlow).where(OIDCFlow.state_hash == token_hash(state)))
    if not flow:
        return fail("unknown_state")
    claimed = db.execute(update(OIDCFlow).where(
        OIDCFlow.id == flow.id, OIDCFlow.browser_hash == token_hash(binding),
        OIDCFlow.consumed_at.is_(None), OIDCFlow.expires_at > now(),
    ).values(consumed_at=now()))
    if claimed.rowcount != 1:
        return fail("invalid_or_consumed_flow")
    db.commit()
    if error:
        return fail("provider_denied")
    if not code:
        return fail("missing_code")
    try:
        claims = request.app.state.oidc.exchange_and_validate(code, flow.verifier, flow.nonce)
    except OIDCFailure as exc:
        return fail(exc.reason, exc.http_status)
    user = db.scalar(select(User).where(User.telegram_issuer == ISSUER, User.telegram_sub == claims["sub"]))
    display_name = str(claims.get("name") or claims.get("preferred_username") or "Пользователь Telegram").strip()[:120] or "Пользователь Telegram"
    if not user:
        user = User(telegram_issuer=ISSUER, telegram_sub=claims["sub"], telegram_user_id=claims.get("id"), display_name=display_name, timezone="UTC")
        try:
            with db.begin_nested():
                db.add(user)
                db.flush()
        except IntegrityError:
            user = db.scalar(select(User).where(User.telegram_issuer == ISSUER, User.telegram_sub == claims["sub"]))
            if not user:
                return fail("user_identity_conflict")
    old_token = request.cookies.get(settings.session_cookie, "")
    if old_token:
        db.execute(update(BrowserSession).where(BrowserSession.token_hash == token_hash(old_token), BrowserSession.revoked_at.is_(None)).values(revoked_at=now()))
    raw, session = issue_session(db, user, settings.session_ttl_hours)
    db.commit()
    response = RedirectResponse(flow.return_path, status_code=303)
    response.delete_cookie(settings.flow_cookie, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    set_cookie(response, settings, settings.session_cookie, raw, settings.session_ttl_hours * 3600)
    return response


@router.get("/auth/session", response_model=SessionView)
def session_view(request: Request, auth: AuthContext = Depends(current_auth)):
    check_same_origin(request)
    return SessionView(user=UserView.model_validate(auth.user), csrf_token=auth.session.csrf_token, expires_at=auth.session.expires_at)


@router.get("/users/me", response_model=UserView, tags=["users"])
def me(auth: AuthContext = Depends(current_auth)):
    return auth.user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, auth: AuthContext = Depends(write_auth), db: Session = Depends(get_db)):
    auth.session.revoked_at = now()
    db.commit()
    response = Response(status_code=204)
    settings = request.app.state.settings
    response.delete_cookie(settings.session_cookie, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    return response
