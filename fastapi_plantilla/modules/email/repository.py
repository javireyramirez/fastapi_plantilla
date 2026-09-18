import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.email.models import EmailLog

__all__ = ["EmailLogRepository"]


class EmailLogRepository(BaseRepository[EmailLog]):
    """Repository for email delivery log records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(EmailLog, session)

    async def get_by_job_id_and_attempt(
        self, job_id: uuid.UUID, attempt: int
    ) -> EmailLog | None:
        """Find an email log entry by job_id and attempt number."""
        stmt = select(EmailLog).where(
            EmailLog.job_id == job_id,
            EmailLog.attempt == attempt,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_next_attempt(self, job_id: uuid.UUID | None) -> int:
        """Calculate next attempt index for a given job_id."""
        if not job_id:
            return 1
        from sqlalchemy import func  # noqa: PLC0415

        stmt = select(func.coalesce(func.max(EmailLog.attempt), 0) + 1).where(
            EmailLog.job_id == job_id
        )
        res = await self.session.execute(stmt)
        return int(res.scalar_one() or 1)
