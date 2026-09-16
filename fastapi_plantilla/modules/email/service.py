from loguru import logger

from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.transports import BaseTransport

__all__ = ["EmailService"]


class EmailService:
    """Service for building and sending email messages."""

    def __init__(self, transport: BaseTransport, renderer: TemplateRenderer) -> None:
        """Initialize email service with transport and renderer."""
        self.transport = transport
        self.renderer = renderer

    def create_builder(self) -> EmailBuilder:
        """Create a new EmailBuilder instance."""
        return EmailBuilder(renderer=self.renderer)

    async def send(
        self,
        email: EmailPayload | EmailBuilder,
        fail_silently: bool = False,
    ) -> bool:
        """
        Send email payload or build directly from builder.

        Returns True if sent successfully, or False if sending failed and
        fail_silently is True. If fail_silently is False, exceptions are raised.
        """
        payload = email.build() if isinstance(email, EmailBuilder) else email
        try:
            await self.transport.send(payload=payload)
            return True
        except Exception as exc:
            logger.error(
                f"Failed to send email to {payload.to} "
                f"with subject '{payload.subject}': {exc}"
            )
            if not fail_silently:
                raise
            return False
