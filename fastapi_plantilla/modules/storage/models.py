from sqlalchemy import (
    BigInteger,
    Boolean,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    OwnedMixin,
    PolymorphicTargetMixin,
    UUID7PrimaryKeyMixin,
)

__all__ = ["Document"]


class Document(
    UUID7PrimaryKeyMixin,
    PolymorphicTargetMixin,
    OwnedMixin,
    AuditFieldsMixin,
    OptimisticLockMixin,
    Base,
):
    """Polymorphic storage document associating files with any domain entity."""

    __tablename__ = "sys_documents"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_key: Mapped[str] = mapped_column(
        String(500), unique=True, index=True, nullable=False
    )
    content_type: Mapped[str] = mapped_column(
        String(100), default="application/octet-stream", nullable=False
    )
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    extension: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_uploaded: Mapped[bool] = mapped_column(
        Boolean, default=False, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_sys_documents_entity", "entity_type", "entity_id"),)
