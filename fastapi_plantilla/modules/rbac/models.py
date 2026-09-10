import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_plantilla.core.crud.schema import ScopeType
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    TimestampMixin,
    UUID7PrimaryKeyMixin,
)
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["Role", "RoleAssignment", "RolePermission", "SystemModule"]


class SystemModule(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Catalog of functional modules protected by RBAC."""

    __tablename__ = "sys_modules"

    code: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category: Mapped[str] = mapped_column(
        String(50), default="system", server_default="system", nullable=False
    )
    category_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category_icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, index=True, nullable=False
    )
    is_trasheable: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )

    @property
    def slug(self) -> str:
        """Slug alias for code."""
        return self.code


class Role(UUID7PrimaryKeyMixin, AuditFieldsMixin, OptimisticLockMixin, Base):
    """Security role grouping permissions."""

    __tablename__ = "rbac_roles"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )
    assignments: Mapped[list["RoleAssignment"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class RolePermission(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Permission entry mapping a role, a system module, an action, and access scope."""

    __tablename__ = "rbac_role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("rbac_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sys_modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[RbacActions] = mapped_column(
        SQLEnum(RbacActions, native_enum=False, length=20), nullable=False
    )
    scope: Mapped[ScopeType] = mapped_column(
        SQLEnum(ScopeType, native_enum=False, length=20),
        default=ScopeType.OWN,
        nullable=False,
    )

    role: Mapped["Role"] = relationship(back_populates="permissions")
    module: Mapped["SystemModule"] = relationship(
        back_populates="permissions", lazy="selectin"
    )

    @property
    def module_code(self) -> str:
        """Module code slug of assigned permission."""
        return self.module.code if self.module else ""

    __table_args__ = (
        UniqueConstraint(
            "role_id", "module_id", "action", name="uq_role_permission_action"
        ),
    )


class RoleAssignment(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Polymorphic role assignment to either a user or a team."""

    __tablename__ = "rbac_role_assignments"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("rbac_roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    role: Mapped["Role"] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint(
            "role_id", "entity_type", "entity_id", name="uq_role_assignment"
        ),
    )
