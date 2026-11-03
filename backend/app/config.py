from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: Literal["production", "development", "test"] = "production"
    database_url: str = Field(default="postgresql+psycopg://eduproject@localhost:5432/eduproject", repr=False)
    public_origin: str = "https://localhost"
    cookie_secure: bool = True
    session_ttl_hours: int = Field(default=24, ge=1, le=168)
    telegram_client_id: str = ""
    telegram_client_secret: SecretStr = SecretStr("")
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    @model_validator(mode="after")
    def validate_origin(self):
        parsed = urlsplit(self.public_origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("PUBLIC_ORIGIN must be an origin without a trailing slash or credentials")
        if not self.cookie_secure and self.app_env not in {"development", "test"}:
            raise ValueError("Insecure cookies are allowed only in development/test")
        if self.app_env == "production" and parsed.scheme != "https":
            raise ValueError("Production requires HTTPS")
        if self.app_env == "production" and not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("Production requires PostgreSQL")
        return self

    @property
    def session_cookie(self) -> str:
        return "__Host-eduproject_session" if self.cookie_secure else "eduproject_session"

    @property
    def flow_cookie(self) -> str:
        return "__Host-eduproject_flow" if self.cookie_secure else "eduproject_flow"

    @property
    def telegram_redirect_uri(self) -> str:
        return self.public_origin + "/api/v1/auth/telegram/callback"

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_client_id and self.telegram_client_secret.get_secret_value())
