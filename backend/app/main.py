from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, models, projects, tasks
from .config import Settings
from .db import build_engine, make_session_factory
from .errors import APIError, api_error_handler, error_response, validation_error_handler
from .oidc import TelegramOIDC


def create_app(settings: Settings | None = None, engine=None) -> FastAPI:
    settings = settings or Settings()
    application = FastAPI(title="EduProject — Sprint 1", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None,
                          description="First implementation increment. Student invitations, projects and tasks through start. Assignment notification delivery and review/final submission are deferred.")
    application.state.settings = settings
    application.state.engine = engine or build_engine(settings.database_url)
    application.state.session_factory = make_session_factory(application.state.engine)
    application.state.oidc = TelegramOIDC(settings)
    application.add_exception_handler(APIError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc):
        return error_response(request, exc.status_code, "not_found" if exc.status_code == 404 else "http_error", "Объект не найден." if exc.status_code == 404 else "Запрос отклонён.")

    @application.exception_handler(Exception)
    async def internal_error(request: Request, exc):
        # No exception text or SQL parameters: flows and invites contain secrets.
        return error_response(request, 500, "internal_error", "Внутренняя ошибка. Повторите запрос позже.")

    @application.middleware("http")
    async def response_security(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not request.url.path.startswith("/api/docs"):
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if request.url.path.startswith("/api/") or request.url.path.startswith("/invitations/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    application.include_router(auth.router, prefix="/api/v1")
    application.include_router(projects.router, prefix="/api/v1")
    application.include_router(tasks.router, prefix="/api/v1")

    @application.get("/api/v1/auth/configuration", tags=["auth"])
    def auth_configuration():
        return {"telegram_login_configured": settings.telegram_configured}

    @application.get("/health/live", include_in_schema=False)
    def live():
        return {"status": "ok"}

    @application.get("/health/ready", include_in_schema=False)
    def ready():
        try:
            with application.state.engine.connect() as connection:
                # Verify migrated schema, not just a listening database socket.
                connection.execute(text("SELECT version_num FROM alembic_version"))
                connection.execute(text("SELECT id FROM users LIMIT 0"))
        except Exception:
            raise APIError(503, "database_unavailable", "База данных или миграции ещё не готовы.") from None
        return {"status": "ready"}

    application.mount("/assets", StaticFiles(directory=settings.frontend_dir / "assets", check_dir=False), name="assets")

    @application.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/projects", status_code=303)

    def spa_response():
        index_file = settings.frontend_dir / "index.html"
        if not index_file.is_file():
            raise APIError(503, "frontend_unavailable", "Файлы интерфейса ещё не установлены.")
        return FileResponse(index_file, headers={"Cache-Control": "no-store"})

    @application.get("/login", include_in_schema=False)
    @application.get("/projects", include_in_schema=False)
    def spa():
        return spa_response()

    @application.get("/projects/{project_id}", include_in_schema=False)
    def spa_project(project_id: UUID):
        return spa_response()

    @application.get("/invitations/{token}", include_in_schema=False)
    def spa_invitation(token: str):
        return spa_response()

    return application


app = create_app()
