from functools import lru_cache

from fastapi import Depends

from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import (
    BaseTransport,
    ResendTransport,
    SmtpTransport,
)
from fastapi_plantilla.settings import EmailBackend, settings


@lru_cache
def get_template_renderer() -> TemplateRenderer:
    """Get cached TemplateRenderer singleton instance."""
    return TemplateRenderer()


@lru_cache
def get_email_transport() -> BaseTransport:
    """Get cached email transport strategy based on settings."""
    if settings.email_backend == EmailBackend.RESEND:
        return ResendTransport()

    return SmtpTransport()


def get_email_service(
    renderer: TemplateRenderer = Depends(get_template_renderer),
    transport: BaseTransport = Depends(get_email_transport),
) -> EmailService:
    """Dependency provider for EmailService."""
    return EmailService(transport=transport, renderer=renderer)
