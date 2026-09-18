import uuid
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.email.builder import EmailBuilder, EmailPayload
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.transports import BaseTransport

if TYPE_CHECKING:
    from datetime import datetime

    from fastapi_plantilla.modules.jobs.schema import JobResponse
    from fastapi_plantilla.modules.jobs.service import JobService

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

    async def enqueue_send(
        self,
        email: EmailPayload | EmailBuilder,
        session: AsyncSession | None = None,
        job_service: "JobService | None" = None,
        user_id: uuid.UUID | None = None,
        scheduled_at: "datetime | None" = None,
    ) -> "JobResponse | bool":
        """
        Enqueue email delivery into background jobs with automatic retry capabilities.

        If jobs_worker_enabled is False or no database session is provided,
        gracefully degrades to direct synchronous sending without silent drops.
        """
        payload = email.build() if isinstance(email, EmailBuilder) else email

        if not settings.jobs_worker_enabled:
            return await self.send(payload, fail_silently=False)

        from fastapi_plantilla.modules.jobs.repository import (  # noqa: PLC0415
            JobRepository,
        )
        from fastapi_plantilla.modules.jobs.schema import (  # noqa: PLC0415
            JobCreateRequest,
        )
        from fastapi_plantilla.modules.jobs.service import (  # noqa: PLC0415
            JobService,
        )

        if job_service is None:
            if session is None:
                return await self.send(payload, fail_silently=False)
            job_service = JobService(JobRepository(session))

        req = JobCreateRequest(
            name="emails.send",
            payload=payload.model_dump(mode="json"),
            scheduled_at=scheduled_at,
        )
        return await job_service.enqueue(req, created_by_id=user_id)
