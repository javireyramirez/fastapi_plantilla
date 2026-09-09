import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    TimestampMixin,
    UUID7PrimaryKeyMixin,
)

if TYPE_CHECKING:
    from fastapi_plantilla.modules.auth.models import User
    from fastapi_plantilla.modules.rbac.models import Role

__all__ = ["Team", "TeamUser"]


class Team(UUID7PrimaryKeyMixin, AuditFieldsMixin, OptimisticLockMixin, Base):
    """Team model for collaborative organization and permission grouping."""

    __tablename__ = "sys_teams"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    members: Mapped[list["TeamUser"]] = relationship(
        back_populates="team", cascade="all, delete-orphan"
    )
    owner: Mapped["User"] = relationship(
        "User", foreign_keys=[owner_id], lazy="selectin"
    )

    @property
    def members_count(self) -> int:
        """Count of active team members."""
        return len(self.members) if self.members else 0


class TeamUser(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Team membership mapping users to teams with an optional role."""

    __tablename__ = "sys_team_users"

    team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sys_teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("rbac_roles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    team: Mapped["Team"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship("User", lazy="selectin")
    role: Mapped["Role | None"] = relationship("Role", lazy="selectin")

    @property
    def user_name(self) -> str | None:
        """Name of enrolled user."""
        return self.user.name if self.user else None

    @property
    def user_email(self) -> str | None:
        """Email of enrolled user."""
        return self.user.email if self.user else None

    @property
    def role_slug(self) -> str | None:
        """Slug of assigned role."""
        return self.role.slug if self.role else None

    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_user"),)
