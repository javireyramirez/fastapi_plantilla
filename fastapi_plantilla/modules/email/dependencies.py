from functools import lru_cache

from fastapi import Depends

from fastapi_plantilla.core.config import EmailBackend, settings
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import (
    BaseTransport,
    ConsoleTransport,
    MemoryTransport,
    ResendTransport,
    SmtpTransport,
)

__all__ = ["get_email_service", "get_email_transport", "get_template_renderer"]


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
