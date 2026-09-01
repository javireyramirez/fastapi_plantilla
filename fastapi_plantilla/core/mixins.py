import enum
import uuid
from datetime import datetime

import uuid_utils
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

__all__ = [
    "AuditFieldsMixin",
    "OptimisticLockMixin",
    "OwnedMixin",
    "PolymorphicTargetMixin",
    "RecordStatus",
    "TimestampMixin",
    "UUID7PrimaryKeyMixin",
]


class RecordStatus(enum.StrEnum):
    """Enumeration of possible record lifecycle statuses."""

    ACTIVE = "ACTIVE"
    PENDING = "PENDING"
    TRASHED = "TRASHED"
    INACTIVE = "INACTIVE"
    ARCHIVED = "ARCHIVED"
    SUSPENDED = "SUSPENDED"


class UUID7PrimaryKeyMixin:
    """Mixin providing a UUIDv7 primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid_utils.uuid7,
    )


class TimestampMixin:
    """Mixin providing automatic creation and update timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditFieldsMixin(TimestampMixin):
    """Mixin providing timestamp tracking, actor logging, and trash status."""

    status: Mapped[RecordStatus] = mapped_column(
        SQLEnum(RecordStatus, native_enum=False, length=20),
        default=RecordStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
    )
    restored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
    )
    created_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    deleted_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )
    restored_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        default=None,
    )


class OptimisticLockMixin:
    """Mixin providing optimistic concurrency control via a version counter."""

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )


class OwnedMixin:
    """Mixin associating a record with an owner user."""

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )


class PolymorphicTargetMixin:
    """Mixin providing generic polymorphic association to any entity."""

    entity_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
