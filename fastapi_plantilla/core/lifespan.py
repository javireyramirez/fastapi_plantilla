from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastapi_plantilla.core.config import EmailBackend, settings
from fastapi_plantilla.modules.audit.jobs import handle_audit_purge  # noqa: F401
from fastapi_plantilla.modules.audit.listener import setup_audit_listeners
from fastapi_plantilla.modules.email.jobs import handle_email_send  # noqa: F401
from fastapi_plantilla.modules.jobs.models import JobStatus
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.scheduler import calculate_next_scheduled_time
from fastapi_plantilla.modules.jobs.schema import JobCreateRequest, JobFilterParams
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.jobs.worker import BackgroundJobWorker
from fastapi_plantilla.modules.rbac.catalog import sync_system_modules
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.storage.jobs import handle_storage_compress  # noqa: F401
from fastapi_plantilla.modules.trash.jobs import handle_trash_purge  # noqa: F401
from fastapi_plantilla.modules.trash.listener import setup_trash_listeners


def _setup_db(app: FastAPI) -> None:  # pragma: no cover
    """Initialize database engine and session factory on app state."""
    engine = create_async_engine(str(settings.db_url), echo=settings.db_echo)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory


async def _ensure_recurring_maintenance_jobs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:  # pragma: no cover
    """Ensure baseline recurring maintenance jobs are scheduled in PostgreSQL."""
    if not settings.jobs_worker_enabled:
        if settings.trash_purge_enabled or settings.audit_purge_enabled:
            logger.warning(
                "Maintenance purges are enabled but 'jobs_worker_enabled' is False; "
                "recurring background jobs will not execute."
            )
        return

    async with session_factory() as session, session.begin():
        repo = JobRepository(session)
        service = JobService(repo)
        settings_service = SystemSettingService(SystemSettingRepository(session))

        if settings.trash_purge_enabled:
            existing = await service.list_jobs(
                JobFilterParams(
                    name="trash.purge",
                    status=[JobStatus.PENDING, JobStatus.RUNNING],
                    limit=1,
                )
            )
            if existing[1] == 0:
                trash_time = await settings_service.get_value(
                    "trash.purge_time_utc", default="03:00"
                )
                scheduled_at = calculate_next_scheduled_time(
                    trash_time, default_hour=3, default_minute=0
                )
                idempotency_key = f"trash.purge:{scheduled_at.strftime('%Y-%m-%d')}"
                await service.enqueue(
                    JobCreateRequest(
                        name="trash.purge",
                        payload={"limit": 1000},
                        scheduled_at=scheduled_at,
                        idempotency_key=idempotency_key,
                    )
                )
                logger.info(
                    f"Initialized 'trash.purge' scheduled at {scheduled_at.isoformat()}"
                )

        if settings.audit_purge_enabled:
            existing = await service.list_jobs(
                JobFilterParams(
                    name="audit.purge",
                    status=[JobStatus.PENDING, JobStatus.RUNNING],
                    limit=1,
                )
            )

            if existing[1] == 0:
                audit_time = await settings_service.get_value(
                    "audit.purge_time_utc", default="03:30"
                )
                scheduled_at = calculate_next_scheduled_time(
                    audit_time, default_hour=3, default_minute=30
                )
                idempotency_key = f"audit.purge:{scheduled_at.strftime('%Y-%m-%d')}"
                await service.enqueue(
                    JobCreateRequest(
                        name="audit.purge",
                        payload={"limit": 1000},
                        scheduled_at=scheduled_at,
                        idempotency_key=idempotency_key,
                    )
                )
                logger.info(
                    f"Initialized 'audit.purge' scheduled at {scheduled_at.isoformat()}"
                )


def _validate_email_configuration() -> None:
    """Fail fast on invalid or missing email configuration in production."""
    if settings.environment in ("development", "test", "dev"):
        return

    if settings.email_backend == EmailBackend.SMTP and not settings.smtp_host:
        raise ValueError(
            "Production requires 'SMTP_HOST' when EMAIL_BACKEND is 'smtp'."
        )
    if settings.email_backend == EmailBackend.RESEND and not settings.resend_api_key:
        raise ValueError(
            "Production requires 'RESEND_API_KEY' when EMAIL_BACKEND is 'resend'."
        )


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """Manage startup and shutdown lifecycle for FastAPI."""
    _validate_email_configuration()
    _setup_db(app)
    setup_audit_listeners()
    setup_trash_listeners()

    session_factory = app.state.db_session_factory
    try:
        async with session_factory() as session, session.begin():
            await sync_system_modules(session)
    except Exception as exc:
        logger.warning(f"Could not sync system modules on startup: {exc}")

    jobs_worker: BackgroundJobWorker | None = None

    if settings.environment != "test":
        await _ensure_recurring_maintenance_jobs(session_factory)
        if settings.jobs_worker_enabled:
            jobs_worker = BackgroundJobWorker(session_factory)
            jobs_worker.start()

        try:
            from fastapi_plantilla.modules.storage.dependencies import (  # noqa: PLC0415
                get_storage_provider,
            )

            storage_prov = get_storage_provider()
            if hasattr(storage_prov, "ensure_bucket_exists"):
                await storage_prov.ensure_bucket_exists()
        except Exception as exc:
            logger.warning(f"Could not verify storage bucket readiness: {exc}")

    yield

    if jobs_worker:
        await jobs_worker.stop()

    await app.state.db_engine.dispose()
