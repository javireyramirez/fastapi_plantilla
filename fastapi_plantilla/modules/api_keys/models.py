import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OwnedMixin,
    UUID7PrimaryKeyMixin,
)

if TYPE_CHECKING:
    from fastapi_plantilla.modules.auth.models import User

__all__ = ["ApiKey"]


class ApiKey(UUID7PrimaryKeyMixin, OwnedMixin, AuditFieldsMixin, Base):
    """API Key entity for external programmatic machine-to-machine integrations."""

    __tablename__ = "auth_api_keys"

    name: Mapped[str] = mapped_column(String(length=150), nullable=False)
    prefix: Mapped[str] = mapped_column(
        String(length=16), default="ak_live_", nullable=False
    )
    key_hash: Mapped[str] = mapped_column(
        String(length=255),
        unique=True,
        index=True,
        nullable=False,
    )
    masked_key: Mapped[str] = mapped_column(String(length=32), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scopes: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    owner: Mapped["User"] = relationship(
        "User",
        foreign_keys=[owner_id],
        lazy="selectin",
    )
