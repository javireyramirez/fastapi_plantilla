import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy import and_, asc, delete, desc, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import (
    Role,
    RoleAssignment,
    RolePermission,
    SystemModule,
)
from fastapi_plantilla.modules.rbac.schema import RoleAssignmentQueryParams
from fastapi_plantilla.modules.teams.models import Team, TeamUser

__all__ = ["RbacRepository"]


class RbacRepository(BaseRepository[Role]):
    """Repository handling persistence and queries for RBAC system."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(Role, session)

    # ==========================================
    # 1. System Modules
    # ==========================================

    async def get_module_by_id(self, module_id: uuid.UUID) -> SystemModule | None:
        """Fetch module by primary key ID."""
        stmt = select(SystemModule).where(SystemModule.id == module_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_module_by_code(self, code: str) -> SystemModule | None:
        """Fetch module by unique code slug."""
        stmt = select(SystemModule).where(SystemModule.code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_modules(self) -> list[SystemModule]:
        """Fetch all registered modules ordered by category order and sort order."""
        stmt = select(SystemModule).order_by(
            SystemModule.category_order.asc(),
            SystemModule.sort_order.asc(),
            SystemModule.code.asc(),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_module(
        self,
        code: str,
        name: str,
        description: str | None = None,
        category: str = "system",
        category_name: str | None = None,
        category_icon: str | None = None,
        category_order: int = 0,
        icon: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
        is_trasheable: bool = True,
    ) -> SystemModule:
        """Create and persist a new system module."""
        module = SystemModule(
            code=code,
            name=name,
            description=description,
            category=category,
            category_name=category_name,
            category_icon=category_icon,
            category_order=category_order,
            icon=icon,
            sort_order=sort_order,
            is_active=is_active,
            is_trasheable=is_trasheable,
        )
        self.session.add(module)
        await self.session.flush()
        await self.session.refresh(module)
        return module

    # ==========================================
    # 2. Roles
    # ==========================================

    def _role_query(self, load_permissions: bool = True) -> Any:
        stmt = select(Role).where(Role.status != RecordStatus.TRASHED)
        if load_permissions:
            stmt = stmt.options(
                selectinload(Role.permissions).selectinload(RolePermission.module)
            )
        return stmt

    async def get_role_by_id(
        self, role_id: uuid.UUID, load_permissions: bool = True
    ) -> Role | None:
        """Fetch active role by primary key ID."""
        stmt = self._role_query(load_permissions).where(Role.id == role_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_role_by_slug(
        self, slug: str, load_permissions: bool = True
    ) -> Role | None:
        """Fetch active role by slug."""
        stmt = self._role_query(load_permissions).where(Role.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_roles(self, load_permissions: bool = False) -> list[Role]:
        """Fetch all active roles ordered by slug."""
        stmt = self._role_query(load_permissions).order_by(Role.slug.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ==========================================
    # 3. Role Permissions
    # ==========================================

    async def set_role_permissions(
        self, role_id: uuid.UUID, items: list[dict[str, Any]]
    ) -> list[RolePermission]:
        """Atomically replace all permissions for a given role."""
        await self.session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        created = [
            RolePermission(
                role_id=role_id,
                module_id=it["module_id"],
                action=it["action"],
                scope=it["scope"],
            )
            for it in items
        ]
        self.session.add_all(created)
        await self.session.flush()
        role = await self.session.get(Role, role_id)
        if role:
            self.session.expire(role, ["permissions"])
        return created

    # ==========================================
    # 4. Role Assignments
    # ==========================================

    async def assign_role(
        self, role_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
    ) -> RoleAssignment:
        """Assign role to an entity (USER or TEAM) idempotently."""
        stmt = select(RoleAssignment).where(
            RoleAssignment.role_id == role_id,
            RoleAssignment.entity_type == entity_type.upper(),
            RoleAssignment.entity_id == entity_id,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        assignment = RoleAssignment(
            role_id=role_id,
            entity_type=entity_type.upper(),
            entity_id=entity_id,
        )
        self.session.add(assignment)
        await self.session.flush()
        await self.session.refresh(assignment)
        return assignment

    async def unassign_role(
        self, role_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
    ) -> bool:
        """Remove role assignment from an entity."""
        stmt = delete(RoleAssignment).where(
            RoleAssignment.role_id == role_id,
            RoleAssignment.entity_type == entity_type.upper(),
            RoleAssignment.entity_id == entity_id,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount) > 0
        return False

    async def get_assignments(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[RoleAssignment]:
        """Fetch all role assignments for a specific entity."""
        stmt = select(RoleAssignment).where(
            func.upper(RoleAssignment.entity_type) == entity_type.strip().upper(),
            RoleAssignment.entity_id == entity_id,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def _build_assignment_conditions(
        self, params: RoleAssignmentQueryParams
    ) -> list[Any]:
        """Construct SQL filter clauses for role assignment queries."""
        conditions: list[Any] = []
        if params.role_id:
            conditions.append(RoleAssignment.role_id == params.role_id)
        if params.user_id:
            conditions.append(
                (func.upper(RoleAssignment.entity_type) == "USER")
                & (RoleAssignment.entity_id == params.user_id)
            )
        if params.team_id:
            conditions.append(
                (func.upper(RoleAssignment.entity_type) == "TEAM")
                & (RoleAssignment.entity_id == params.team_id)
            )
        if params.entity_type:
            conditions.append(
                func.upper(RoleAssignment.entity_type)
                == params.entity_type.strip().upper()
            )
        if params.assigned_from:
            conditions.append(RoleAssignment.created_at >= params.assigned_from)
        if params.assigned_to:
            conditions.append(RoleAssignment.created_at <= params.assigned_to)
        return conditions

    def _resolve_assignment_sort(self, sort_by: str, sort_order: str) -> Any:
        """Resolve order_by clause for role assignment queries."""
        sort_field: Any = RoleAssignment.created_at
        sort_by_lower = sort_by.lower()
        if sort_by_lower in ("roleid", "role_id"):
            sort_field = RoleAssignment.role_id
        elif sort_by_lower in ("entitytype", "entity_type"):
            sort_field = RoleAssignment.entity_type

        return asc(sort_field) if sort_order.lower() == "asc" else desc(sort_field)

    async def list_assignments(
        self, params: RoleAssignmentQueryParams
    ) -> tuple[list[tuple[RoleAssignment, Role, User | None, Team | None]], int]:
        """Fetch paginated role assignments with joined role, user, and team models."""
        stmt = (
            select(RoleAssignment, Role, User, Team)
            .join(Role, Role.id == RoleAssignment.role_id)
            .outerjoin(
                User,
                and_(
                    func.upper(RoleAssignment.entity_type) == "USER",
                    RoleAssignment.entity_id == User.id,
                ),
            )
            .outerjoin(
                Team,
                and_(
                    func.upper(RoleAssignment.entity_type) == "TEAM",
                    RoleAssignment.entity_id == Team.id,
                ),
            )
        )
        count_stmt = select(func.count(RoleAssignment.id))
        conditions = self._build_assignment_conditions(params)

        if conditions:
            stmt = stmt.where(and_(*conditions))
            count_stmt = count_stmt.where(and_(*conditions))

        total = (await self.session.scalar(count_stmt)) or 0
        order_expr = self._resolve_assignment_sort(params.sort_by, params.sort_order)
        stmt = (
            stmt.order_by(order_expr)
            .offset((params.page - 1) * params.limit)
            .limit(params.limit)
        )

        res = await self.session.execute(stmt)
        rows = list(res.all())
        return rows, total  # type: ignore[return-value]

    async def get_assignment_by_id(
        self, assignment_id: uuid.UUID, role_id: uuid.UUID | None = None
    ) -> tuple[RoleAssignment, Role, User | None, Team | None] | None:
        """Fetch a single role assignment by ID with joined role, user, and team."""
        stmt = (
            select(RoleAssignment, Role, User, Team)
            .join(Role, Role.id == RoleAssignment.role_id)
            .outerjoin(
                User,
                and_(
                    func.upper(RoleAssignment.entity_type) == "USER",
                    RoleAssignment.entity_id == User.id,
                ),
            )
            .outerjoin(
                Team,
                and_(
                    func.upper(RoleAssignment.entity_type) == "TEAM",
                    RoleAssignment.entity_id == Team.id,
                ),
            )
            .where(RoleAssignment.id == assignment_id)
        )
        if role_id is not None:
            stmt = stmt.where(RoleAssignment.role_id == role_id)

        res = await self.session.execute(stmt)
        return res.first()  # type: ignore[return-value]

    # ==========================================
    # 5. Effective User Permissions Resolution
    # ==========================================

    async def get_user_effective_permissions(
        self, user_id: uuid.UUID
    ) -> tuple[list[str], list[tuple[str, str, str]], list[uuid.UUID], list[uuid.UUID]]:
        """Fetch effective roles and permissions for a user.

        Combines direct user roles, team membership roles, and team-assigned roles.
        Returns a tuple of:
        (role_slugs, permissions, team_ids, teammate_ids)
        """
        # 1. User teams & team membership roles
        team_stmt = select(TeamUser).where(TeamUser.user_id == user_id)
        team_users = list((await self.session.execute(team_stmt)).scalars().all())
        team_ids = [tu.team_id for tu in team_users]

        role_ids: set[uuid.UUID] = {
            tu.role_id for tu in team_users if tu.role_id is not None
        }

        # 2. Teammates
        teammate_ids: set[uuid.UUID] = set()
        if team_ids:
            teammates_stmt = select(TeamUser.user_id).where(
                TeamUser.team_id.in_(team_ids)
            )
            teammates_res = await self.session.execute(teammates_stmt)
            teammate_ids = set(teammates_res.scalars().all())

        # 3. Direct user and team-level role assignments
        assign_conds = [
            (RoleAssignment.entity_type == "USER")
            & (RoleAssignment.entity_id == user_id)
        ]
        if team_ids:
            assign_conds.append(
                (RoleAssignment.entity_type == "TEAM")
                & (RoleAssignment.entity_id.in_(team_ids))
            )
        assign_stmt = select(RoleAssignment.role_id).where(or_(*assign_conds))
        role_ids.update((await self.session.execute(assign_stmt)).scalars().all())

        if not role_ids:
            return [], [], team_ids, list(teammate_ids)

        # 4. Fetch Role details and permissions
        roles_stmt = select(Role).where(Role.id.in_(role_ids))
        roles = list((await self.session.execute(roles_stmt)).scalars().all())
        role_slugs = [r.slug for r in roles]

        permissions_list: list[tuple[str, str, str]] = []
        for role in roles:
            for perm in role.permissions:
                if perm.module and perm.module.is_active:
                    permissions_list.append(
                        (perm.module.code, str(perm.action), str(perm.scope))
                    )

        return role_slugs, permissions_list, team_ids, list(teammate_ids)
