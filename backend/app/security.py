import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import APIError
from .models import BrowserSession, Member, Project, User, now


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def random_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class AuthContext:
    user: User
    session: BrowserSession


def optional_auth(request: Request, db: Session) -> AuthContext | None:
    raw = request.cookies.get(request.app.state.settings.session_cookie, "")
    if len(raw) < 32 or len(raw) > 256:
        return None
    session = db.scalar(select(BrowserSession).where(
        BrowserSession.token_hash == token_hash(raw), BrowserSession.revoked_at.is_(None),
        BrowserSession.expires_at > now(),
    ))
    if not session:
        return None
    user = db.get(User, session.user_id)
    return AuthContext(user=user, session=session) if user else None


def current_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    auth = optional_auth(request, db)
    if not auth:
        raise APIError(401, "authentication_required", "Войдите через Telegram.")
    return auth


def check_same_origin(request: Request, required: bool = False):
    origin = request.headers.get("origin")
    if (required and origin is None) or (origin is not None and origin != request.app.state.settings.public_origin):
        raise APIError(403, "origin_rejected", "Запрос должен поступать с адреса приложения.")
    if request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        raise APIError(403, "origin_rejected", "Запрос должен поступать с адреса приложения.")


def write_auth(request: Request, auth: AuthContext = Depends(current_auth)) -> AuthContext:
    check_same_origin(request, required=True)
    supplied = request.headers.get("x-csrf-token", "")
    if not 32 <= len(supplied) <= 256 or not secrets.compare_digest(supplied.encode("utf-8"), auth.session.csrf_token.encode("ascii")):
        raise APIError(403, "csrf_rejected", "Обновите страницу и повторите действие.")
    return auth


def issue_session(db: Session, user: User, ttl_hours: int):
    raw = random_token()
    session = BrowserSession(user_id=user.id, token_hash=token_hash(raw), csrf_token=random_token(), expires_at=now() + timedelta(hours=ttl_hours))
    db.add(session)
    db.flush()
    return raw, session


def get_membership(db: Session, project_id, user_id, lock: bool = False) -> tuple[Project, Member]:
    row = db.execute(select(Project, Member).join(Member, Member.project_id == Project.id).where(
        Project.id == project_id, Member.user_id == user_id, Member.active.is_(True),
    )).first()
    if not row:
        raise APIError(404, "not_found", "Объект не найден или недоступен.")
    if lock:
        # Every write locks project before task/invitation, giving consistent
        # lock ordering and serializing membership acceptance/revocation.
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update().execution_options(populate_existing=True))
        return project, row[1]
    return row[0], row[1]


def require_lead(member: Member):
    if member.role != "lead":
        raise APIError(403, "role_forbidden", "Действие доступно тимлиду проекта.")


def require_open(project: Project):
    if project.status not in {"active", "changes_requested"}:
        raise APIError(409, "project_locked", "Проект закрыт для изменений.")


def expected_version(request: Request) -> int:
    value = request.headers.get("if-match")
    if value is None:
        raise APIError(428, "precondition_required", "Для изменения передайте актуальный ETag в If-Match.")
    if len(value) > 64 or not re.fullmatch(r'"[1-9][0-9]*"', value):
        raise APIError(422, "invalid_etag", 'If-Match должен содержать версию в кавычках, например "1".')
    return int(value[1:-1])


def check_version(actual: int, expected: int):
    if actual != expected:
        raise APIError(412, "version_conflict", "Объект изменился. Обновите данные и повторите действие.")
