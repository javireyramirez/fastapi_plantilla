import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import TimestampMixin, UUID7PrimaryKeyMixin

__all__ = ["EmailLog"]


class EmailLog(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Traceability log for outbound email messages."""

    __tablename__ = "sys_email_logs"

    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("sys_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    to: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
    )
    template_name: Mapped[str | None] = mapped_column(
        String(length=150),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(length=50),
        default="PENDING",
        nullable=False,
        index=True,
    )
    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (Index("ix_sys_email_logs_job_attempt", "job_id", "attempt"),)
