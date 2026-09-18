import uuid
from datetime import UTC, datetime

from fastapi_plantilla.core.crud.service_base import BaseCRUDService
from fastapi_plantilla.modules.email.models import EmailLog
from fastapi_plantilla.modules.email.repository import EmailLogRepository
from fastapi_plantilla.modules.email.schema import EmailLogStatus

__all__ = ["EmailLogService"]


class EmailLogService(BaseCRUDService[EmailLog]):
    """Service for managing and querying outbound email traceability logs."""

    def __init__(self, repository: EmailLogRepository) -> None:
        super().__init__(repository)
        self.repository: EmailLogRepository = repository

    async def record_attempt(
        self,
        to: list[str],
        subject: str,
        template_name: str | None,
        job_id: uuid.UUID | None = None,
        attempt: int = 1,
        status: EmailLogStatus = EmailLogStatus.PENDING,
        error: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> EmailLog:
        """Create or update an email log entry for an execution attempt."""
        log = None
        if job_id:
            log = await self.repository.get_by_job_id_and_attempt(job_id, attempt)

        if log:
            log.status = str(status)
            log.error = error
            if status == EmailLogStatus.SENT:
                log.sent_at = datetime.now(UTC)
            await self.repository.session.flush()
            return log

        log = EmailLog(
            to=to,
            subject=subject,
            template_name=template_name,
            job_id=job_id,
            attempt=attempt,
            status=str(status),
            error=error,
            sent_at=datetime.now(UTC) if status == EmailLogStatus.SENT else None,
            user_id=user_id,
        )
        return await self.repository.create(log)
