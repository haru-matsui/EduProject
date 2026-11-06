from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.models import Member, Task
from app.oidc import pkce_challenge
from app.tasks import task_flags
from .conftest import ROOT
from .helpers import API, create_project, create_task


def test_pkce_rfc7636_known_vector():
    assert pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_deadline_window_boundaries_and_completed_tasks():
    clock = datetime(2026, 10, 28, 10, tzinfo=UTC)
    assert task_flags(clock - timedelta(microseconds=1), "todo", clock) == (True, False)
    assert task_flags(clock, "todo", clock) == (False, False)
    assert task_flags(clock + timedelta(hours=24), "in_progress", clock) == (False, True)
    assert task_flags(clock + timedelta(hours=24, microseconds=1), "todo", clock) == (False, False)
    assert task_flags(clock - timedelta(days=1), "done", clock) == (False, False)


@pytest.mark.parametrize("changes", [{"public_origin": "http://example.com"}, {"cookie_secure": False}, {"public_origin": "https://example.com/"}, {"public_origin": "https://user:pass@example.com"}, {"database_url": "sqlite://"}])
def test_production_rejects_unsafe_settings(changes):
    with pytest.raises(ValidationError):
        Settings(app_env="production", _env_file=None, **changes)


def test_misspelled_environment_cannot_bypass_production_policy():
    with pytest.raises(ValidationError):
        Settings(app_env="prodution", public_origin="http://example.com", _env_file=None)


def check_database_constraints(env):
    client, user = env.identity()
    one, two = create_project(client), create_project(client)
    task = create_task(client, one["id"])
    with env.app.state.session_factory() as db:
        members = db.scalars(select(Member)).all()
        foreign = next(member for member in members if str(member.project_id) == two["id"])
        row = db.get(Task, __import__("uuid").UUID(task["id"]))
        row.assignee_member_id = foreign.id
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.add(Member(project_id=foreign.project_id, user_id=user.id, role="student"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    inspector = inspect(env.engine)
    assert {"users", "projects", "project_members", "tasks", "invitations", "sessions", "oidc_flows", "audit_events", "alembic_version"} <= set(inspector.get_table_names())


def test_migration_and_database_constraints(env):
    check_database_constraints(env)


@pytest.mark.postgres
def test_postgres_migration_constraints_and_roundtrip(postgres_env):
    check_database_constraints(postgres_env)
    config = Config(str(ROOT / "alembic.ini"))
    with postgres_env.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert "tasks" not in inspect(postgres_env.engine).get_table_names()
    with postgres_env.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    assert "tasks" in inspect(postgres_env.engine).get_table_names()
