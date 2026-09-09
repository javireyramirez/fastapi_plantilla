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


class StorageBackend(enum.StrEnum):
    """Supported storage backends."""

    LOCAL = "local"
    S3 = "s3"
    AZURE = "azure"
    GCS = "gcs"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server / App
    host: str = "127.0.0.1"
    port: int = 8000
    workers_count: int = 1
    reload: bool = False
    environment: str = "dev"
    log_level: LogLevel = LogLevel.INFO

    # Frontend
    frontend_url: str | None

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
    initial_superadmin_email: str | None = None
    initial_superadmin_password: str | None = None
    initial_superadmin_name: str = "Super Admin"

    # Email
    email_backend: EmailBackend | None = None
    emails_from_email: str | None = None
    emails_from_name: str | None = None

    # Email - SMTP
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool | None = None
    smtp_ssl: bool | None = None

    # Email - Resend
    resend_api_key: str | None = None

    # Storage
    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_bucket: str = "fastapi-plantilla-bucket"
    storage_local_path: Path = TEMP_DIR / "fastapi_plantilla_storage"
    storage_local_base_url: str | None = None

    # Storage - S3 / MinIO
    storage_s3_endpoint_url: str | None = None
    storage_s3_access_key: str | None = None
    storage_s3_secret_key: str | None = None
    storage_s3_region: str = "us-east-1"

    # Storage - Azure Blob
    storage_azure_connection_string: str | None = None
    storage_azure_account_name: str | None = None
    storage_azure_account_key: str | None = None
    storage_azure_container: str | None = None

    # Storage - Google Cloud Storage
    storage_gcs_credentials_file: str | None = None
    storage_gcs_project: str | None = None

    # Trash & Retention
    trash_retention_days: int = 30
    trash_purge_interval_hours: int = 24
    trash_purge_enabled: bool = True

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
