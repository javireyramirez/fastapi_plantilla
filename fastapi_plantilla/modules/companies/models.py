from typing import TYPE_CHECKING

from sqlalchemy import Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    OwnedMixin,
    UUID7PrimaryKeyMixin,
)

if TYPE_CHECKING:
    from fastapi_plantilla.modules.auth.models import User

__all__ = ["Company"]


class Company(
    UUID7PrimaryKeyMixin,
    OwnedMixin,
    AuditFieldsMixin,
    OptimisticLockMixin,
    Base,
):
    """Company business entity model."""

    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    nif: Mapped[str] = mapped_column(
        String(50), unique=False, index=False, nullable=False
    )
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_companies_nif_active",
            "nif",
            unique=True,
            postgresql_where=text("status != 'TRASHED'"),
            sqlite_where=text("status != 'TRASHED'"),
        ),
    )

    owner: Mapped["User | None"] = relationship(
        "User",
        foreign_keys="Company.owner_id",
        lazy="selectin",
    )
