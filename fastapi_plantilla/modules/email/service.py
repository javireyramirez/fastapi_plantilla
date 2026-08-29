from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.transports import BaseTransport


class EmailService:
    """Service for building and sending email messages."""

    def __init__(self, transport: BaseTransport, renderer: TemplateRenderer) -> None:
        """Initialize email service with transport and renderer."""
        self.transport = transport
        self.renderer = renderer

    def create_builder(self) -> EmailBuilder:
        """Create a new EmailBuilder instance."""
        return EmailBuilder(renderer=self.renderer)

    async def send(self, email: EmailPayload | EmailBuilder) -> None:
        """Send email payload or build directly from builder."""
        payload = email.build() if isinstance(email, EmailBuilder) else email
        await self.transport.send(payload=payload)
