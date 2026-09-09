import uuid

from fastapi import Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import Session as AuthSession
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import Role, RoleAssignment

__all__ = ["UserAdminRepository"]


class UserAdminRepository(BaseRepository[User]):
    """Repository handling persistence and queries for admin user management."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(User, session)

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch user by ID."""
        stmt = select(User).where(
            User.id == user_id, User.status != RecordStatus.TRASHED
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Fetch user by email."""
        stmt = select(User).where(
            User.email == email, User.status != RecordStatus.TRASHED
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_users(
        self,
        search: str | None = None,
        is_active: bool | None = None,
        is_super_admin: bool | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[User]:
        """Fetch paginated users with optional search and status filters."""
        stmt = (
            select(User)
            .where(User.status != RecordStatus.TRASHED)
            .order_by(User.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    User.name.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
        if is_super_admin is not None:
            stmt = stmt.where(User.is_super_admin == is_super_admin)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_users(
        self,
        search: str | None = None,
        is_active: bool | None = None,
        is_super_admin: bool | None = None,
    ) -> int:
        """Count users matching filter parameters."""
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.status != RecordStatus.TRASHED)
        )

        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    User.name.ilike(search_pattern),
                    User.email.ilike(search_pattern),
                )
            )
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)
        if is_super_admin is not None:
            stmt = stmt.where(User.is_super_admin == is_super_admin)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

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
