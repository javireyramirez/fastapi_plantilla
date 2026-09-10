import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import UUID7PrimaryKeyMixin

__all__ = ["AuditLog"]


class AuditLog(UUID7PrimaryKeyMixin, Base):
    """Centralized immutable audit trail recording all domain and security events."""

    __tablename__ = "sys_audit_logs"

    entity_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        index=True,
        nullable=True,
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
        nullable=False,
    )
