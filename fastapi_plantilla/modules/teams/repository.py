import uuid
from datetime import datetime

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.teams.models import Team, TeamUser

__all__ = ["TeamRepository"]


class TeamRepository(BaseRepository[Team]):
    """Repository managing persistence for teams and memberships."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(Team, session)

    async def get_by_id(self, team_id: uuid.UUID) -> Team | None:
        """Fetch team by primary key ID."""
        stmt = (
            select(Team)
            .options(selectinload(Team.members))
            .where(Team.id == team_id, Team.status != RecordStatus.TRASHED)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Team | None:
        """Fetch team by slug."""
        stmt = (
            select(Team)
            .options(selectinload(Team.members))
            .where(Team.slug == slug, Team.status != RecordStatus.TRASHED)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_teams(
        self,
        team_ids: list[uuid.UUID] | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Team]:
        """Fetch paginated list of teams, filtering by permitted IDs and dates."""
        stmt = (
            select(Team)
            .options(selectinload(Team.members))
            .where(Team.status != RecordStatus.TRASHED)
            .order_by(Team.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        if team_ids is not None:
            stmt = stmt.where(Team.id.in_(team_ids))
        if created_at_from is not None:
            stmt = stmt.where(Team.created_at >= created_at_from)
        if created_at_to is not None:
            stmt = stmt.where(Team.created_at <= created_at_to)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_teams(
        self,
        team_ids: list[uuid.UUID] | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
    ) -> int:
        """Count teams matching filter."""
        stmt = (
            select(func.count())
            .select_from(Team)
            .where(Team.status != RecordStatus.TRASHED)
        )
        if team_ids is not None:
            stmt = stmt.where(Team.id.in_(team_ids))
        if created_at_from is not None:
            stmt = stmt.where(Team.created_at >= created_at_from)
        if created_at_to is not None:
            stmt = stmt.where(Team.created_at <= created_at_to)

        result = await self.session.execute(stmt)
        return result.scalar() or 0

    # ==========================================
    # Team Memberships
    # ==========================================

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        """Check whether a user exists in the database."""
        stmt = select(select(User.id).where(User.id == user_id).exists())
        return bool(await self.session.scalar(stmt))

    async def get_member(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> TeamUser | None:
        """Fetch member record by team and user IDs."""
        stmt = (
            select(TeamUser)
            .options(selectinload(TeamUser.user), selectinload(TeamUser.role))
            .where(TeamUser.team_id == team_id, TeamUser.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_members(self, team_id: uuid.UUID) -> list[TeamUser]:
        """Fetch all members of a team with user and role details."""
        stmt = (
            select(TeamUser)
            .options(selectinload(TeamUser.user), selectinload(TeamUser.role))
            .where(TeamUser.team_id == team_id)
            .order_by(TeamUser.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_member(
        self,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        role_id: uuid.UUID | None = None,
    ) -> TeamUser:
        """Add user to team membership."""
        member = TeamUser(team_id=team_id, user_id=user_id, role_id=role_id)
        self.session.add(member)
        await self.session.flush()
        return await self.get_member(team_id, user_id) or member

    async def update_member_role(
        self, member: TeamUser, role_id: uuid.UUID | None
    ) -> TeamUser:
        """Update role of an existing team member."""
        member.role_id = role_id
        await self.session.flush()
        return await self.get_member(member.team_id, member.user_id) or member

    async def remove_member(self, member: TeamUser) -> None:
        """Delete member from team."""
        await self.session.delete(member)
        await self.session.flush()
