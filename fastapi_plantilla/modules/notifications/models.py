import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import TimestampMixin, UUID7PrimaryKeyMixin

__all__ = ["Notification", "NotificationType"]


class NotificationType(enum.StrEnum):
    """Types of notifications dispatched in the system."""

    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_FAILED = "JOB_FAILED"
    SYSTEM = "SYSTEM"


class Notification(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """In-app persistent notification for users."""

    __tablename__ = "sys_notifications"

    recipient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    # Database column name is 'type', mapped to notification_type to avoid A003
    notification_type: Mapped[NotificationType] = mapped_column(
        "type",
        SQLEnum(NotificationType, native_enum=False, length=20),
        default=NotificationType.INFO,
        nullable=False,
        index=True,
    )

    # Polymorphic reference to source entity
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    action_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
    )

    __table_args__ = (
        Index("idx_sys_notifications_recipient_read", "recipient_id", "read_at"),
        Index(
            "idx_sys_notifications_recipient_created",
            "recipient_id",
            "created_at",
        ),
        Index("idx_sys_notifications_entity", "entity_type", "entity_id"),
    )

    def __repr__(self) -> str:
        return f"<Notification(id='{self.id}', type='{self.notification_type}')>"
