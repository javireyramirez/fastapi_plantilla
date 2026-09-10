import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    OwnedMixin,
    PolymorphicTargetMixin,
    TimestampMixin,
    UUID7PrimaryKeyMixin,
)

__all__ = ["TrashItem"]


class TrashItem(
    UUID7PrimaryKeyMixin,
    PolymorphicTargetMixin,
    OwnedMixin,
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
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_sys_trash_bin_entity"),
        Index(
            "ix_sys_trash_bin_target_entity",
            "target_entity_type",
            "target_entity_id",
        ),
    )
