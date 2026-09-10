import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    PolymorphicTargetMixin,
    TimestampMixin,
    UUID7PrimaryKeyMixin,
)

__all__ = ["TrashItem"]


class TrashItem(
    UUID7PrimaryKeyMixin,
    PolymorphicTargetMixin,
    TimestampMixin,
    Base,
):
    """Centralized trash bin tracking soft-deleted entities across all modules."""

    __tablename__ = "sys_trash_bin"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_entity_type: Mapped[str | None] = mapped_column(
        String(50), nullable=True, index=True
    )
    target_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )
    target_entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    deleted_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    data_backup: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sys_trash_bin_entity"),
        Index(
            "ix_sys_trash_bin_target_entity",
            "target_entity_type",
            "target_entity_id",
        ),
    )
