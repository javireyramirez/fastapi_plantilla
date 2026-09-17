import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import TimestampMixin, UUID7PrimaryKeyMixin
from fastapi_plantilla.modules.jobs.constants import (
    DEFAULT_LEASE_DURATION_SECONDS,
    DEFAULT_MAX_RETRIES,
)

__all__ = ["Job", "JobStatus"]


class JobStatus(enum.StrEnum):
    """Execution status for background jobs."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Job(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Background asynchronous job persisted in the database."""

    __tablename__ = "sys_jobs"

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        SQLEnum(JobStatus, native_enum=False, length=20),
        default=JobStatus.PENDING,
        nullable=False,
        index=True,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Polymorphic attachment to any domain entity
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Payloads & Results
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Execution attempts & retries
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_MAX_RETRIES, nullable=False
    )

    # Fencing and lease recovery
    lease_token: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_duration_seconds: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_LEASE_DURATION_SECONDS, nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Scheduling timestamps
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Idempotency key
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Ownership / Actor
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        Index("idx_sys_jobs_entity", "entity_type", "entity_id"),
        Index(
            "idx_sys_jobs_claim_pending",
            "scheduled_at",
            "created_at",
            postgresql_where=(status == JobStatus.PENDING),
        ),
        Index(
            "idx_sys_jobs_claim_expired_leases",
            "lease_expires_at",
            postgresql_where=(status == JobStatus.RUNNING),
        ),
        Index(
            "uq_sys_jobs_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where=(status.in_([JobStatus.PENDING, JobStatus.RUNNING])),
        ),
    )

    def __repr__(self) -> str:
        return f"<Job(id='{self.id}', name='{self.name}', status='{self.status}')>"
