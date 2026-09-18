import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import PaginationParams

__all__ = [
    "EmailLogCreate",
    "EmailLogPaginationParams",
    "EmailLogResponse",
    "EmailLogStatus",
    "EmailLogUpdate",
]


class EmailLogStatus(StrEnum):
    """Lifecycle and delivery status of an email message."""

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class EmailLogResponse(BaseModel):
    """Representation of an email log entry for audit and tracking."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID | None = None
    attempt: int = 1
    to: list[str]
    subject: str
    template_name: str | None = None
    status: EmailLogStatus
    error: str | None = None
    sent_at: datetime | None = None
    user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class EmailLogCreate(BaseModel):
    """Payload schema to record an email log entry."""

    to: list[str]
    subject: str
    template_name: str | None = None
    status: EmailLogStatus = EmailLogStatus.PENDING
    job_id: uuid.UUID | None = None
    attempt: int = 1
    error: str | None = None
    sent_at: datetime | None = None
    user_id: uuid.UUID | None = None


class EmailLogUpdate(BaseModel):
    """Payload schema to update an existing email log entry."""

    status: EmailLogStatus | None = None
    error: str | None = None
    sent_at: datetime | None = None


class EmailLogPaginationParams(PaginationParams):
    """Query parameters for filtering and paginating email logs."""

    status: EmailLogStatus | None = Field(
        default=None,
        description="Filter by delivery status (PENDING, SENT, FAILED)",
    )
    template_name: str | None = Field(
        default=None,
        description="Filter by template name",
    )
