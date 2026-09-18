from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import EmailBackend, settings
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.email.email_log_service import EmailLogService
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.repository import EmailLogRepository
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import (
    BaseTransport,
    ConsoleTransport,
    MemoryTransport,
    ResendTransport,
    SmtpTransport,
)

__all__ = [
    "create_email_log_service",
    "get_email_log_service",
    "get_email_service",
    "get_email_transport",
    "get_template_renderer",
]


@lru_cache
def get_template_renderer() -> TemplateRenderer:
    """Get cached TemplateRenderer singleton instance."""
    return TemplateRenderer()


@lru_cache
def get_email_transport() -> BaseTransport:
    """Get cached email transport strategy based on settings."""
    if settings.email_backend == EmailBackend.CONSOLE:
        return ConsoleTransport()
    if settings.email_backend == EmailBackend.MEMORY:
        return MemoryTransport()
    if settings.email_backend == EmailBackend.RESEND:
        return ResendTransport()

    # Fallback to console transport if no smtp host is configured in dev/test
    if not settings.smtp_host and settings.environment in ("development", "test"):
        return ConsoleTransport()

    return SmtpTransport()


def get_email_service(
    renderer: TemplateRenderer = Depends(get_template_renderer),
    transport: BaseTransport = Depends(get_email_transport),
) -> EmailService:
    """Dependency provider for EmailService."""
    return EmailService(transport=transport, renderer=renderer)


def get_email_log_service(
    session: AsyncSession = Depends(get_db_session),
) -> EmailLogService:
    """Dependency provider for EmailLogService."""
    return EmailLogService(EmailLogRepository(session))


def create_email_log_service(session: AsyncSession) -> EmailLogService:
    """Factory helper creating EmailLogService from an existing session."""
    return EmailLogService(EmailLogRepository(session))
