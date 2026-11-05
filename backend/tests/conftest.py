import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db import build_engine
from app.main import create_app
from app.models import User
from app.security import issue_session

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://testserver"


def migrate(engine, revision="head"):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@dataclass
class Environment:
    app: object
    engine: object
    settings: Settings

    def client(self):
        return TestClient(self.app, base_url=ORIGIN, follow_redirects=False)

    def identity(self, name="Student"):
        # Only tests can mint identities directly. There is no HTTP test login.
        with self.app.state.session_factory() as db:
            user = User(telegram_issuer="https://test.invalid", telegram_sub=str(uuid4()), display_name=name, timezone="UTC")
            db.add(user)
            db.flush()
            raw, session = issue_session(db, user, 24)
            db.commit()
            client = self.client()
            client.cookies.set(self.settings.session_cookie, raw)
            client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": session.csrf_token})
            return client, user


def environment(engine):
    settings = Settings(app_env="test", public_origin=ORIGIN, cookie_secure=True, database_url="sqlite://", telegram_client_id="", telegram_client_secret="", _env_file=None)
    migrate(engine)
    return Environment(create_app(settings, engine), engine, settings)


@pytest.fixture
def env(tmp_path):
    engine = build_engine("sqlite:///" + (tmp_path / "test.sqlite3").as_posix())
    result = environment(engine)
    yield result
    engine.dispose()


@pytest.fixture
def postgres_env():
    """Separate random schema; never drop/reset the supplied database."""
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured; PostgreSQL was not exercised")
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must be PostgreSQL")
    schema = "test_eduproject_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict({"options": "-csearch_path=" + schema})
    engine = build_engine(scoped_url.render_as_string(hide_password=False))
    try:
        yield environment(engine)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
