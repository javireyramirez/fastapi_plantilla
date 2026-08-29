import enum
from pathlib import Path
from tempfile import gettempdir

from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

TEMP_DIR = Path(gettempdir())


class LogLevel(enum.StrEnum):
    """Possible log levels."""

    NOTSET = "NOTSET"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class EmailBackend(enum.StrEnum):
    """Supported email backends."""

    SMTP = "smtp"
    RESEND = "resend"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server / App
    host: str = "127.0.0.1"
    port: int = 8000
    workers_count: int = 1
    reload: bool = False
    environment: str = "dev"
    log_level: LogLevel = LogLevel.INFO

    # Database
    db_host: str
    db_port: int | None = None
    db_user: str | None = None
    db_pass: str | None = None
    db_base: str | None = None
    db_echo: bool = False
    db_pool_size: int | None = None
    db_max_overflow: int | None = None

    # Authentication & Security
    auth_secret: str
    google_client_id: str | None = None
    google_client_secret: str | None = None
    session_cookie_name: str = "fastapi_session"
    session_expire_days: int = 7
    cookie_secure: bool = False

    # Email
    email_backend: EmailBackend | None = None
    emails_from_email: str | None = None
    emails_from_name: str | None = None
    frontend_url: str | None = None

    # Email - SMTP
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool | None = None
    smtp_ssl: bool | None = None

    # Email - Resend
    resend_api_key: str | None = None

    @property
    def db_url(self) -> URL:
        """Assemble database URL from settings."""
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_pass,
            path=f"/{self.db_base}" if self.db_base else "",
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FASTAPI_PLANTILLA_",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()  # type: ignore[call-arg]
