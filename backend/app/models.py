from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime, TypeDecorator

from .db import Base


def now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """PostgreSQL timestamptz; normalize SQLite values for portable unit tests."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Timezone-aware datetime required")
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("telegram_issuer", "telegram_sub", name="uq_users_identity"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    telegram_issuer: Mapped[str] = mapped_column(String(200))
    telegram_sub: Mapped[str] = mapped_column(String(256))
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    display_name: Mapped[str] = mapped_column(String(120))
    group_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint("status IN ('active','final_review','changes_requested','completed')", name="ck_project_status"), CheckConstraint("version >= 1", name="ck_project_version"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    goal: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Member(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),
        UniqueConstraint("id", "project_id", name="uq_member_id_project"),
        CheckConstraint("role IN ('lead','student','teacher')", name="ck_member_role"),
        Index("uq_active_lead", "project_id", unique=True, postgresql_where=text("active AND role = 'lead'"), sqlite_where=text("active = 1 AND role = 'lead'")),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Invitation(Base):
    __tablename__ = "invitations"
    __table_args__ = (CheckConstraint("role IN ('student','teacher')", name="ck_invitation_role"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("project_members.id"))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        ForeignKeyConstraint(["assignee_member_id", "project_id"], ["project_members.id", "project_members.project_id"], name="fk_task_assignee_same_project"),
        CheckConstraint("status IN ('todo','in_progress','in_review','done')", name="ck_task_status"),
        CheckConstraint("priority IN ('low','medium','high')", name="ck_task_priority"),
        CheckConstraint("version >= 1", name="ck_task_version"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("project_members.id"))
    assignee_member_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime)
    priority: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="todo")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class BrowserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)


class OIDCFlow(Base):
    __tablename__ = "oidc_flows"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(128))
    return_path: Mapped[str] = mapped_column(String(400))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AuditEvent(Base):
    """Internal history only; user-facing history and Telegram delivery are deferred."""
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    actor_member_id: Mapped[UUID] = mapped_column(ForeignKey("project_members.id"))
    action: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=now)
