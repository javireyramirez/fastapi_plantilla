import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import Session as AuthSession
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import Role, RoleAssignment
from fastapi_plantilla.modules.teams.models import Team, TeamUser

__all__ = ["UserAdminRepository"]


class UserAdminRepository(BaseRepository[User]):
    """Repository handling persistence and queries for admin user management."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(User, session)

    async def get_by_email(self, email: str) -> User | None:
        """Fetch user by email."""
        return await self.find_first(
            User.email == email, User.status != RecordStatus.TRASHED
        )

    async def count_active_superadmins(self) -> int:
        """Count active, non-trashed super admin users."""
        stmt = select(func.count(User.id)).where(
            User.is_super_admin.is_(True),
            User.is_active.is_(True),
            User.status != RecordStatus.TRASHED,
        )
        result = await self.session.execute(stmt)
        return int(result.scalar() or 0)

    async def invalidate_user_sessions(self, user_id: uuid.UUID) -> int:
        """Invalidate all active sessions for a user."""
        stmt = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.is_valid.is_(True))
            .values(is_valid=False)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    async def invalidate_bulk_sessions(self, user_ids: list[uuid.UUID]) -> int:
        """Invalidate active sessions for multiple users."""
        if not user_ids:
            return 0
        stmt = (
            update(AuthSession)
            .where(AuthSession.user_id.in_(user_ids), AuthSession.is_valid.is_(True))
            .values(is_valid=False)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    async def bulk_set_active_status(
        self, user_ids: list[uuid.UUID], is_active: bool
    ) -> int:
        """Update active status for multiple users in bulk, protecting system users."""
        if not user_ids:
            return 0
        stmt = (
            update(User)
            .where(User.id.in_(user_ids), User.is_system.is_(False))
            .values(is_active=is_active)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        """Fetch assigned role slugs for a single user."""
        stmt = (
            select(Role.slug)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(
                RoleAssignment.entity_type == "USER",
                RoleAssignment.entity_id == user_id,
            )
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_roles_for_users(
        self, user_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Fetch role slugs mapped by user_id for a batch of users (solves N+1)."""
        if not user_ids:
            return {}
        stmt = (
            select(RoleAssignment.entity_id, Role.slug)
            .join(Role, Role.id == RoleAssignment.role_id)
            .where(
                RoleAssignment.entity_type == "USER",
                RoleAssignment.entity_id.in_(user_ids),
            )
        )
        res = await self.session.execute(stmt)
        roles_map: dict[uuid.UUID, list[str]] = {uid: [] for uid in user_ids}
        for uid, slug in res.all():
            roles_map[uid].append(slug)
        return roles_map

    async def get_user_team_assignments(
        self, user_id: uuid.UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch paginated teams assigned to a user."""
        base = (
            select(Team.id, Team.name, Team.slug, TeamUser.role_id, TeamUser.created_at)
            .join(TeamUser, TeamUser.team_id == Team.id)
            .where(TeamUser.user_id == user_id, Team.status != RecordStatus.TRASHED)
        )
        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar() or 0
        rows = (
            await self.session.execute(
                base.order_by(Team.name.asc()).offset(skip).limit(limit)
            )
        ).all()
        return [
            {"id": r[0], "name": r[1], "slug": r[2], "role_id": r[3], "joined_at": r[4]}
            for r in rows
        ], total

    async def assign_user_teams(
        self, user_id: uuid.UUID, team_ids: list[uuid.UUID]
    ) -> int:
        """Assign multiple teams to a user ignoring existing ones."""
        if not team_ids:
            return 0
        existing_stmt = select(TeamUser.team_id).where(
            TeamUser.user_id == user_id, TeamUser.team_id.in_(team_ids)
        )
        existing = set((await self.session.execute(existing_stmt)).scalars().all())
        to_add = [tid for tid in team_ids if tid not in existing]
        for tid in to_add:
            self.session.add(TeamUser(user_id=user_id, team_id=tid))
        await self.session.flush()
        return len(to_add)

    async def remove_user_teams(
        self, user_id: uuid.UUID, team_ids: list[uuid.UUID]
    ) -> int:
        """Remove team memberships for a user."""
        if not team_ids:
            return 0
        stmt = delete(TeamUser).where(
            TeamUser.user_id == user_id, TeamUser.team_id.in_(team_ids)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return int(res.rowcount) if isinstance(res, CursorResult) else 0

    async def get_user_role_assignments(
        self, user_id: uuid.UUID, skip: int = 0, limit: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """Fetch paginated roles assigned to a user with timestamp."""
        base = (
            select(Role.id, Role.name, Role.slug, RoleAssignment.created_at)
            .join(RoleAssignment, RoleAssignment.role_id == Role.id)
            .where(
                RoleAssignment.entity_type == "USER",
                RoleAssignment.entity_id == user_id,
            )
        )
        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar() or 0
        rows = (
            await self.session.execute(
                base.order_by(Role.name.asc()).offset(skip).limit(limit)
            )
        ).all()
        return [
            {"id": r[0], "name": r[1], "slug": r[2], "assigned_at": r[3]} for r in rows
        ], total

    async def remove_user_roles_bulk(
        self, user_id: uuid.UUID, role_ids: list[uuid.UUID]
    ) -> int:
        """Remove multiple assigned roles from a user."""
        if not role_ids:
            return 0
        stmt = delete(RoleAssignment).where(
            RoleAssignment.entity_type == "USER",
            RoleAssignment.entity_id == user_id,
            RoleAssignment.role_id.in_(role_ids),
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return int(res.rowcount) if isinstance(res, CursorResult) else 0
