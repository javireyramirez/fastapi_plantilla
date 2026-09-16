from typing import Any

from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import TimestampMixin, UUID7PrimaryKeyMixin

__all__ = ["SystemSetting"]


class SystemSetting(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Dynamic configuration and feature flags stored in database."""

    __tablename__ = "sys_settings"

    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    value: Mapped[Any] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(
        String(50),
        default="general",
        server_default="general",
        index=True,
        nullable=False,
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), index=True, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<SystemSetting(key='{self.key}', "
            f"category='{self.category}', is_public={self.is_public})>"
        )
