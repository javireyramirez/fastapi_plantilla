import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fastapi_plantilla.core.config import EmailBackend, settings
from fastapi_plantilla.modules.audit.listener import setup_audit_listeners
from fastapi_plantilla.modules.audit.service import purge_expired_audit
from fastapi_plantilla.modules.jobs.worker import BackgroundJobWorker
from fastapi_plantilla.modules.rbac.catalog import sync_system_modules
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.trash.listener import setup_trash_listeners
from fastapi_plantilla.modules.trash.service import purge_expired_trash


def _setup_db(app: FastAPI) -> None:  # pragma: no cover
    """Initialize database engine and session factory on app state."""
    engine = create_async_engine(str(settings.db_url), echo=settings.db_echo)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory


async def _trash_purge_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:  # pragma: no cover
    """Background task to periodically purge expired trash items."""
    interval_seconds = max(3600, settings.trash_purge_interval_hours * 3600)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            async with session_factory() as session, session.begin():
                settings_service = SystemSettingService(
                    SystemSettingRepository(session)
                )
                auto_enabled = await settings_service.get_value(
                    "trash.auto_purge_enabled", default=True
                )
                if not auto_enabled:
                    continue
                count = await purge_expired_trash(
                    session, settings_service=settings_service
                )
                if count > 0:
                    logger.info(f"Auto-purged {count} expired trash items")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error in periodic trash purge worker: {exc}")


async def _audit_purge_worker(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:  # pragma: no cover
    """Background task to periodically purge expired audit records."""
    interval_seconds = max(3600, settings.audit_purge_interval_hours * 3600)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            async with session_factory() as session, session.begin():
                settings_service = SystemSettingService(
                    SystemSettingRepository(session)
                )
                auto_enabled = await settings_service.get_value(
                    "audit.auto_purge_enabled", default=True
                )
                if not auto_enabled:
                    continue
                count = await purge_expired_audit(
                    session, settings_service=settings_service
                )
                if count > 0:
                    logger.info(f"Auto-purged {count} expired audit records")
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(f"Error in periodic audit purge worker: {exc}")


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

    purge_tasks: list[asyncio.Task[None]] = []
    jobs_worker: BackgroundJobWorker | None = None

    if settings.environment != "test":
        if settings.trash_purge_enabled:
            purge_tasks.append(
                asyncio.create_task(_trash_purge_worker(session_factory))
            )
        if settings.audit_purge_enabled:
            purge_tasks.append(
                asyncio.create_task(_audit_purge_worker(session_factory))
            )
        if settings.jobs_worker_enabled:
            jobs_worker = BackgroundJobWorker(session_factory)
            jobs_worker.start()

    yield

    if jobs_worker:
        await jobs_worker.stop()

    for task in purge_tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    await app.state.db_engine.dispose()
