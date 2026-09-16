import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.jobs.models import JobStatus

__all__ = [
    "JobCancelResponse",
    "JobContext",
    "JobCreateRequest",
    "JobFilterParams",
    "JobResponse",
    "JobRetryResponse",
]


class JobResponse(BaseModel):
    """Schema for public job representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: JobStatus
    progress: int
    progress_message: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    attempts: int
    max_retries: int
    scheduled_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class JobCreateRequest(BaseModel):
    """Payload to programmatically enqueue a job."""

    name: str = Field(..., min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    max_retries: int = Field(default=3, ge=0, le=10)
    lease_duration_seconds: int = Field(default=300, ge=10, le=3600)
    scheduled_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)


class JobFilterParams(BaseModel):
    """Query filters for listing jobs."""

    status: JobStatus | None = None
    name: str | None = None
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class JobCancelResponse(BaseModel):
    """Response returned when a job is cancelled."""

    id: uuid.UUID
    status: JobStatus
    message: str


class JobRetryResponse(BaseModel):
    """Response returned when a failed job is rescheduled."""

    id: uuid.UUID
    status: JobStatus
    message: str


@dataclass
class JobContext[P: BaseModel | dict[str, Any]]:
    """Runtime execution context provided to a job handler."""

    job_id: uuid.UUID
    name: str
    payload: P
    entity_type: str | None
    entity_id: uuid.UUID | None
    lease_token: int
    session: AsyncSession
    _update_progress_fn: Callable[
        [uuid.UUID, int, int, str | None],
        Coroutine[Any, Any, None],
    ]
    _check_cancelled_fn: Callable[
        [uuid.UUID, int],
        Coroutine[Any, Any, bool],
    ]
    _last_cancelled_check: float = field(default=0.0, init=False)
    _cached_cancelled_val: bool = field(default=False, init=False)

    async def update_progress(self, progress: int, message: str | None = None) -> None:
        """Update job progress (0..100) and message.

        Checks lease token and cancellation.

        Raises:
            JobCancelledError: If the job was cancelled by user.
            JobLeaseLostError: If another worker overtook the job lease.
        """
        clamped = max(0, min(100, progress))
        await self._update_progress_fn(self.job_id, self.lease_token, clamped, message)

    async def is_cancelled(self) -> bool:
        """Check if job has been cancelled with a 1-second in-memory throttle."""
        now = time.monotonic()
        if now - self._last_cancelled_check < 1.0:
            return self._cached_cancelled_val

        self._cached_cancelled_val = await self._check_cancelled_fn(
            self.job_id, self.lease_token
        )
        self._last_cancelled_check = now
        return self._cached_cancelled_val
