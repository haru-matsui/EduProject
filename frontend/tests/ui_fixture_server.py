"""QA-only real DOM harness: synthetic authentication, never real Telegram E2E.

Run using backend/.venv Python. Production code and configuration are untouched.
"""
import argparse
import asyncio
from contextlib import contextmanager
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile

APP_ROOT = Path(__file__).resolve().parents[2]
BACKEND = APP_ROOT / "backend"
QA_NOTICE = "QA fixture authentication; synthetic users; not real Telegram/team acceptance"
ISSUER = "https://qa.example.invalid"


def ports(value):
    number = int(value)
    if not 1024 <= number <= 65535:
        raise argparse.ArgumentTypeError("QA port must be between 1024 and 65535")
    return number


def reserve_ports(numbers):
    """Reserve both IPv4 loopback sockets atomically before creating any DB."""
    sockets = []
    try:
        for port in numbers:
            connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(connection)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            connection.bind(("127.0.0.1", port))
            connection.listen(128)
            connection.setblocking(False)
        return sockets
    except BaseException:
        for connection in sockets:
            connection.close()
        raise


def check_paths():
    if not BACKEND.is_dir() or not (APP_ROOT / "frontend/index.html").is_file():
        raise RuntimeError("QA harness must remain inside EduProject_App/frontend/tests")
    if (BACKEND / ".env").exists():
        raise RuntimeError("QA import refuses a backend/.env file; no dotenv file will be read")
    qa = APP_ROOT / "qa"
    if qa.exists() and (not qa.is_dir() or qa.resolve().parent != APP_ROOT.resolve()):
        raise RuntimeError("QA directory must be directly inside EduProject_App")
    return qa


def build_fixtures(run_dir, lead_port, student_port):
    # app.main has a module-level create_app(). Guard even that import with a
    # QA Settings subclass: it cannot load a .env or point to a production DB.
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app import config as config_module
    RealSettings = config_module.Settings
    database = run_dir / "synthetic.sqlite3"
    if database.exists() or database.resolve().parent != run_dir.resolve():
        raise RuntimeError("Only a new database inside the fresh QA directory is allowed")
    database_url = "sqlite:///" + database.as_posix()
    defaults = dict(app_env="test", database_url=database_url, cookie_secure=False,
                    public_origin=f"http://127.0.0.1:{lead_port}", session_ttl_hours=24,
                    telegram_client_id="", telegram_client_secret="",
                    frontend_dir=APP_ROOT / "frontend", _env_file=None)

    class FixtureSettings(RealSettings):
        model_config = {**RealSettings.model_config, "env_file": None}

        def __init__(self, **values):
            super().__init__(**{**defaults, **values})

    config_module.Settings = FixtureSettings
    try:
        from app import main as main_module
    finally:
        config_module.Settings = RealSettings
    main_module.Settings = RealSettings
    main_module.app.state.engine.dispose()  # Import-only QA engine, never served.

    from alembic import command
    from alembic.config import Config
    from fastapi import Request
    from fastapi.responses import RedirectResponse, Response
    from sqlalchemy import event
    from app.db import build_engine, make_session_factory
    from app.models import User
    from app.security import issue_session

    engine = build_engine(database_url)
    @event.listens_for(engine, "connect")
    def qa_sqlite_timeout(connection, _):
        connection.execute("PRAGMA busy_timeout=5000")
    migration = Config(str(BACKEND / "alembic.ini"))
    with engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.upgrade(migration, "head")
    factory = make_session_factory(engine)
    with factory() as db:
        lead = User(telegram_issuer=ISSUER, telegram_sub="qa-lead", telegram_user_id=None,
                    display_name="Тестовый тимлид (QA)", timezone="UTC")
        student = User(telegram_issuer=ISSUER, telegram_sub="qa-student", telegram_user_id=None,
                       display_name="Тестовый студент (QA)", timezone="UTC")
        db.add_all([lead, student]); db.commit()
        identities = [lead.id, student.id]

    applications = []
    origins = [f"http://127.0.0.1:{lead_port}", f"http://localhost:{student_port}"]
    for origin, identity in zip(origins, identities):
        settings = FixtureSettings(public_origin=origin, _env_file=None)
        application = main_module.create_app(settings, engine=engine)
        application.state.qa_fixture_identity = identity

        @application.middleware("http")
        async def qa_notice_and_loopback(request: Request, call_next):
            configured = request.app.state.settings.public_origin.removeprefix("http://")
            try:
                local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            except ValueError:
                local = False
            if not local or request.headers.get("host", "").lower() != configured.lower():
                return Response("QA fixture accepts only its configured loopback origin", status_code=403,
                                headers={"X-EduProject-QA-Fixture": QA_NOTICE})
            response = await call_next(request)
            response.headers["X-EduProject-QA-Fixture"] = QA_NOTICE
            response.headers["Cache-Control"] = "no-store"
            if response.headers.get("content-type", "").startswith("text/html"):
                body = b"".join([part async for part in response.body_iterator])
                banner = ('<aside role="note" class="alert alert-warning rounded-0 mb-0 text-center small">'
                          + QA_NOTICE + '</aside>').encode("utf-8")
                body = body.replace(b"<body>", b"<body>" + banner, 1)
                headers = dict(response.headers)
                headers.pop("content-length", None)
                return Response(body, status_code=response.status_code, headers=headers)
            return response

        @application.get("/_qa/enter", include_in_schema=False)
        def qa_enter(request: Request):
            # QA-only registration on this in-memory application instance.
            with request.app.state.session_factory() as db:
                user = db.get(User, request.app.state.qa_fixture_identity)
                if not user or user.telegram_issuer != ISSUER or user.telegram_sub not in {"qa-lead", "qa-student"}:
                    return Response("Synthetic fixture identity unavailable", status_code=503)
                raw, _ = issue_session(db, user, request.app.state.settings.session_ttl_hours)
                db.commit()
            response = RedirectResponse("/projects", status_code=303)
            response.set_cookie(request.app.state.settings.session_cookie, raw, max_age=86400,
                                httponly=True, secure=False, samesite="lax", path="/")
            response.headers["X-EduProject-QA-Fixture"] = QA_NOTICE
            return response

        applications.append(application)
    return applications, engine, database, origins


async def serve(applications, sockets):
    import uvicorn

    class OwnedServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield  # One common handler below stops both servers in this PID.

    servers = [OwnedServer(uvicorn.Config(app, host="127.0.0.1", port=connection.getsockname()[1],
                access_log=False, log_level="critical", proxy_headers=False))
               for app, connection in zip(applications, sockets)]

    def stop_owned_servers(_signal, _frame):
        for server in servers:
            if server.should_exit:
                server.force_exit = True
            server.should_exit = True

    previous = {kind: signal.signal(kind, stop_owned_servers) for kind in (signal.SIGINT, signal.SIGTERM)}
    try:
        await asyncio.gather(*(server.serve(sockets=[connection]) for server, connection in zip(servers, sockets)))
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lead-port", type=ports, default=8011)
    parser.add_argument("--student-port", type=ports, default=8012)
    parser.add_argument("--check", action="store_true", help="Check paths/config/available ports without importing the app, creating a DB or starting servers")
    arguments = parser.parse_args()
    if arguments.lead_port == arguments.student_port:
        parser.error("QA ports must be different")
    sockets = []
    engine = None
    try:
        qa = check_paths()
        sockets = reserve_ports([arguments.lead_port, arguments.student_port])
        urls = [f"http://127.0.0.1:{arguments.lead_port}/_qa/enter", f"http://localhost:{arguments.student_port}/_qa/enter"]
        if arguments.check:
            print(json.dumps({"lead_url": urls[0], "student_url": urls[1], "pid": os.getpid(), "database_path": "not_created_check_only"}), flush=True)
            return 0
        qa.mkdir(exist_ok=True)
        run_dir = Path(tempfile.mkdtemp(prefix="ui-fixture-", dir=qa)).resolve()
        if run_dir.parent != qa.resolve() or not run_dir.name.startswith("ui-fixture-"):
            raise RuntimeError("Fresh fixture directory escaped the QA root")
        applications, engine, database, _ = build_fixtures(run_dir, arguments.lead_port, arguments.student_port)
        metadata = {"lead_url": urls[0], "student_url": urls[1], "pid": os.getpid(), "database_path": str(database)}
        (run_dir / "fixture.json").write_text(json.dumps({**metadata, "qa_notice": QA_NOTICE}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(metadata, ensure_ascii=False), flush=True)
        asyncio.run(serve(applications, sockets))
        return 0
    except (OSError, RuntimeError) as error:
        print("QA fixture startup refused (" + type(error).__name__ + "). Check loopback ports and QA-path guards.", file=sys.stderr)
        return 1
    finally:
        for connection in sockets:
            connection.close()
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
