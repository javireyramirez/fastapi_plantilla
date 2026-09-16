"""Email module for transactional email delivery and templating."""

from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.dependencies import (
    get_email_service,
    get_email_transport,
    get_template_renderer,
)
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.email.transports import (
    BaseTransport,
    ConsoleTransport,
    MemoryTransport,
    ResendTransport,
    SmtpTransport,
)

__all__ = [
    "BaseTransport",
    "ConsoleTransport",
    "EmailBuilder",
    "EmailPayload",
    "EmailService",
    "MemoryTransport",
    "ResendTransport",
    "SmtpTransport",
    "TemplateRenderer",
    "get_email_service",
    "get_email_transport",
    "get_template_renderer",
]
