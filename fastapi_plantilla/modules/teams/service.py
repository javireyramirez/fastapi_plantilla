import uuid
from typing import Any

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.teams.repository import TeamRepository
from fastapi_plantilla.modules.teams.schema import (
    TeamMemberAdd,
    TeamMemberResponse,
    TeamMemberUpdate,
)

__all__ = ["TeamService"]


class TeamService(BaseAuditService[Team]):
    """Business logic for team management and team memberships."""

    resource_name: str = "Team"
    display_field: str = "name"
    mask_forbidden_as_not_found: bool = True

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

    def can_reassign_owner(
        self, target_owner_id: uuid.UUID, scope: ScopeContext | None
    ) -> bool:
        """Check whether the client is authorized to reassign the team owner.

        NOTE: Intentionally stricter than BaseOwnedService.can_reassign_owner.
        Teams represent organization boundaries, so ownership cannot be
        delegated to peers/teammates unless caller is superadmin or global admin.
        """
        if scope is None:
            return False
        if scope.is_super_admin or scope.scope == ScopeType.GLOBAL:
            return True
        return bool(scope.user_id is not None and target_owner_id == scope.user_id)

    async def _get_team_or_404(
        self, team_id: uuid.UUID, scope: ScopeContext, allow_trashed: bool = False
    ) -> Team:
        """Fetch team by ID and enforce scope boundary via SQL filters."""
        scope_filters = self.build_scope_filters(scope)
        where: list[Any] = []
        if not allow_trashed:
            where.append(Team.status != RecordStatus.TRASHED)
        team = await self.repository.find_first(
            Team.id == team_id, *scope_filters, *where
        )
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Team not found",
            )
        return team

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Team:
        """Create team atomically and auto-enroll creator as member."""
        slug = (
            getattr(data, "slug", None)
            if isinstance(data, BaseModel)
            else data.get("slug")
        )
        if slug:
            existing = await self.repository.get_by_slug(slug)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Team with slug '{slug}' already exists",
                )

        creator_uuid = (
            uuid.UUID(str(user_id))
            if user_id
            else (owner_id or (scope.user_id if scope else None))
        )
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        if creator_uuid and not payload.get("owner_id"):
            payload["owner_id"] = creator_uuid

        try:
            async with self.repository.session.begin_nested():
                team = await super().create(
                    payload,
                    user_id=user_id,
                    owner_id=owner_id or creator_uuid,
                    scope=scope,
                    allow_immutable=allow_immutable,
                    options=options,
                )
                if creator_uuid:
                    await self.repository.add_member(
                        team_id=team.id,
                        user_id=creator_uuid,
                        role_id=None,
                    )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team with slug '{slug}' already exists",
            ) from exc

        refreshed = await self.repository.get_by_id(team.id)
        return refreshed or team

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Team:
        """Update team attributes, enforcing owner reassignments and auto-enrolling.

        Auto-enrolls the new owner as a team member if not already enrolled.
        """
        update_data = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        new_owner_id: uuid.UUID | None = None
        if "owner_id" in update_data and update_data["owner_id"] is not None:
            new_owner_id = uuid.UUID(str(update_data["owner_id"]))
            if not self.can_reassign_owner(new_owner_id, scope):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: insufficient permissions to reassign team owner",
                )
            if not await self.repository.user_exists(new_owner_id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="New owner user not found",
                )

        async with self.repository.session.begin_nested():
            updated = await super().update(
                id,
                data,
                *where,
                expected_version=expected_version,
                user_id=user_id,
                scope=scope,
                allow_immutable=allow_immutable,
                options=options,
            )
            if new_owner_id is not None and not await self.repository.get_member(
                id, new_owner_id
            ):
                await self.repository.add_member(id, new_owner_id)

        refreshed = await self.repository.get_by_id(id)
        return refreshed or updated

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Team:
        """Restore team ensuring no active team with the same slug exists."""
        opts = options or WriteOptions(user_id=user_id, scope=scope)
        scope_filters = self.build_scope_filters(opts.scope)
        team = await self.repository.find_first(Team.id == id, *scope_filters, *where)
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
        try:
            return await super().restore(
                id, *where, user_id=user_id, scope=scope, options=options
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot restore team: slug is already in use by an active team."
                ),
            ) from exc

    async def bulk_restore(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk restore teams ensuring no active teams conflict with their slugs."""
        opts = options or WriteOptions(user_id=user_id, scope=scope)
        scope_filters = self.build_scope_filters(opts.scope)
        teams = await self.repository.find_many(
            Team.id.in_(req.ids),
            Team.status == RecordStatus.TRASHED,
            *scope_filters,
            *where,
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
        try:
            return await super().bulk_restore(
                req, *where, user_id=user_id, scope=scope, options=options
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot restore teams: one or more slugs are already in use.",
            ) from exc

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
        team = await self._get_team_or_404(team_id, scope)

        if not await self.repository.user_exists(data.user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found",
            )

        if data.role_id and not await self.repository.role_exists(data.role_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found",
            )

        existing_member = await self.repository.get_member(team_id, data.user_id)
        if existing_member:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this team",
            )

        try:
            member = await self.repository.add_member(
                team_id=team_id,
                user_id=data.user_id,
                role_id=data.role_id,
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User is already a member of this team",
            ) from exc

        await self._emit_audit(
            item=member,
            action="ADD_MEMBER",
            user_id=scope.user_id,
            entity_id=team.id,
            entity_name=team.name,
            details=f"Added member {data.user_id} to team {team.name}",
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
        team = await self._get_team_or_404(team_id, scope)

        if data.role_id and not await self.repository.role_exists(data.role_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found",
            )

        member = await self.repository.get_member(team_id, user_id)
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found in team",
            )

        updated = await self.repository.update_member_role(member, role_id=data.role_id)
        await self._emit_audit(
            item=updated,
            action="UPDATE_MEMBER",
            user_id=scope.user_id,
            entity_id=team.id,
            entity_name=team.name,
            details=f"Updated role for member {user_id} in team {team.name}",
        )
        return TeamMemberResponse.model_validate(updated)

    async def remove_member(
        self, team_id: uuid.UUID, user_id: uuid.UUID, scope: ScopeContext
    ) -> None:
        """Remove member from team."""
        team = await self._get_team_or_404(team_id, scope)

        if team.owner_id == user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove team owner from members",
            )

        member = await self.repository.get_member(team_id, user_id)
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Member not found in team",
            )

        await self.repository.remove_member(member)
        await self._emit_audit(
            item=member,
            action="REMOVE_MEMBER",
            user_id=scope.user_id,
            entity_id=team.id,
            entity_name=team.name,
            details=f"Removed member {user_id} from team {team.name}",
        )
