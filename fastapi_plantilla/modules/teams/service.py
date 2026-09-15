import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy import or_

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.teams.repository import TeamRepository
from fastapi_plantilla.modules.teams.schema import (
    TeamCreate,
    TeamMemberAdd,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)

__all__ = ["TeamService"]


class TeamService(BaseAuditService[Team]):
    """Business logic for team management and team memberships."""

    resource_name: str = "Team"
    display_field: str = "name"

    def __init__(self, repository: TeamRepository = Depends()) -> None:
        super().__init__(repository)
        self.repository: TeamRepository = repository

    def build_scope_filters(self, scope: ScopeContext | None = None) -> list[Any]:
        """Build scope filter clauses for teams based on user permissions."""
        if not scope or scope.is_super_admin or scope.scope == ScopeType.GLOBAL:
            return []
        clauses: list[Any] = []
        if scope.team_ids:
            clauses.append(Team.id.in_(scope.team_ids))
        if scope.user_id:
            clauses.append(Team.owner_id == scope.user_id)
        return [or_(*clauses)] if clauses else [Team.id.is_(None)]

    async def _get_team_or_404(
        self, team_id: uuid.UUID, scope: ScopeContext, allow_trashed: bool = False
    ) -> Team:
        """Fetch team by ID and enforce scope boundary, raising 404 if unauthorized."""
        team = await self.repository.get_by_id(team_id)
        if not team or (not allow_trashed and team.status == RecordStatus.TRASHED):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found",
            )
        if (
            not (scope.is_super_admin or scope.scope == ScopeType.GLOBAL)
            and team.id not in scope.team_ids
            and team.owner_id != scope.user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found",
            )
        return team

    async def create_team(
        self, creator_id: uuid.UUID, data: TeamCreate
    ) -> TeamResponse:
        """Create team and automatically enroll creator as member."""
        existing = await self.repository.get_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team with slug '{data.slug}' already exists",
            )

        payload = data.model_dump()
        payload["owner_id"] = creator_id
        team = await self.create(payload, user_id=creator_id, owner_id=creator_id)
        await self.repository.add_member(
            team_id=team.id,
            user_id=creator_id,
            role_id=None,
        )
        refreshed = await self.repository.get_by_id(team.id)
        return TeamResponse.model_validate(refreshed or team)

    async def list_teams(
        self,
        scope: ScopeContext,
        search: str | None = None,
        name: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[TeamResponse]:
        """List teams accessible within the user's scope with optional filters."""
        filter_team_ids: list[uuid.UUID] | None = None
        if not scope.is_super_admin and scope.scope != ScopeType.GLOBAL:
            filter_team_ids = scope.team_ids

        search_term = search or name
        skip = (page - 1) * limit
        teams = await self.repository.list_teams(
            team_ids=filter_team_ids,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            search=search_term,
            skip=skip,
            limit=limit,
        )
        total = await self.repository.count_teams(
            team_ids=filter_team_ids,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            search=search_term,
        )

        return PaginatedResponse(
            data=[TeamResponse.model_validate(t) for t in teams],
            meta=PaginationMeta.create(page=page, limit=limit, total=total),
        )

    async def get_team(self, team_id: uuid.UUID, scope: ScopeContext) -> TeamResponse:
        """Fetch team by ID ensuring scope access (including trashed for inspection)."""
        team = await self._get_team_or_404(team_id, scope, allow_trashed=True)
        return TeamResponse.model_validate(team)

    async def update_team(
        self, team_id: uuid.UUID, data: TeamUpdate, scope: ScopeContext
    ) -> TeamResponse:
        """Update team attributes."""
        team = await self._get_team_or_404(team_id, scope)
        update_data = data.model_dump(exclude_unset=True)
        if update_data:
            await self.update(
                team_id,
                update_data,
                user_id=scope.user_id,
                scope=scope,
            )
        refreshed = await self.repository.get_by_id(team_id)
        return TeamResponse.model_validate(refreshed or team)

    async def delete_team(self, team_id: uuid.UUID, scope: ScopeContext) -> None:
        """Delete team (soft delete synchronized with trash)."""
        await self._get_team_or_404(team_id, scope)
        await self.trash(
            team_id,
            user_id=scope.user_id,
            scope=scope,
        )

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Team:
        """Restore team ensuring no active team with the same slug exists."""
        team = await self.repository.find_first(Team.id == id)
        if team and team.status == RecordStatus.TRASHED:
            existing = await self.repository.get_by_slug(team.slug)
            if existing and existing.id != team.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore team '{team.name}': slug '{team.slug}' "
                        "is already in use by an active team."
                    ),
                )
        return await super().restore(
            id, *where, user_id=user_id, scope=scope, options=options
        )

    async def bulk_restore(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk restore teams ensuring no active teams conflict with their slugs."""
        teams = await self.repository.find_many(
            Team.id.in_(req.ids), Team.status == RecordStatus.TRASHED
        )
        for t in teams:
            existing = await self.repository.get_by_slug(t.slug)
            if existing and existing.id not in req.ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore team '{t.name}': slug '{t.slug}' "
                        "is already in use by an active team."
                    ),
                )
        return await super().bulk_restore(
            req, *where, user_id=user_id, scope=scope, options=options
        )

    # ==========================================
    # Member Operations
    # ==========================================

    async def list_members(
        self, team_id: uuid.UUID, scope: ScopeContext
    ) -> list[TeamMemberResponse]:
        """List members belonging to a team."""
        await self._get_team_or_404(team_id, scope)
        members = await self.repository.list_members(team_id)
        return [TeamMemberResponse.model_validate(m) for m in members]

    async def add_member(
        self, team_id: uuid.UUID, data: TeamMemberAdd, scope: ScopeContext
    ) -> TeamMemberResponse:
        """Enroll a user into team."""
        await self._get_team_or_404(team_id, scope)

        if not await self.repository.user_exists(data.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found",
            )

        existing_member = await self.repository.get_member(team_id, data.user_id)
        if existing_member:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this team",
            )

        member = await self.repository.add_member(
            team_id=team_id,
            user_id=data.user_id,
            role_id=data.role_id,
        )
        return TeamMemberResponse.model_validate(member)

    async def update_member(
        self,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        data: TeamMemberUpdate,
        scope: ScopeContext,
    ) -> TeamMemberResponse:
        """Update role of a team member."""
        await self._get_team_or_404(team_id, scope)

        member = await self.repository.get_member(team_id, user_id)
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found in team",
            )

        updated = await self.repository.update_member_role(member, role_id=data.role_id)
        return TeamMemberResponse.model_validate(updated)

    async def remove_member(
        self, team_id: uuid.UUID, user_id: uuid.UUID, scope: ScopeContext
    ) -> None:
        """Remove member from team."""
        await self._get_team_or_404(team_id, scope)

        member = await self.repository.get_member(team_id, user_id)
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found in team",
            )

        await self.repository.remove_member(member)
