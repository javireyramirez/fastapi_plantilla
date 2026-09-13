import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    MessageResponse,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.teams.dependencies import get_team_service
from fastapi_plantilla.modules.teams.schema import (
    TeamCreate,
    TeamMemberAdd,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)
from fastapi_plantilla.modules.teams.service import TeamService

router = APIRouter(prefix="/teams", tags=["Teams"])


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    data: TeamCreate,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.CREATE)),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Create a new team (creator becomes owner and member)."""
    if not scope.user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User authentication required",
        )
    return await service.create_team(creator_id=scope.user_id, data=data)


@router.get("", response_model=PaginatedResponse[TeamResponse])
async def list_teams(
    search: str | None = Query(default=None),
    name: str | None = Query(default=None),
    created_at_from: datetime | None = Query(default=None),
    created_at_to: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.READ)),
    service: TeamService = Depends(get_team_service),
) -> PaginatedResponse[TeamResponse]:
    """List teams accessible within caller's scope with optional filters."""
    return await service.list_teams(
        scope=scope,
        search=search,
        name=name,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
        page=page,
        limit=limit,
    )


@router.post("/bulk/trash", response_model=BulkResponse)
async def bulk_trash_teams(
    req: BulkIdsRequest,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.DELETE)),
    service: TeamService = Depends(get_team_service),
) -> BulkResponse:
    """Move multiple teams to trash."""
    return await service.bulk_trash(req=req, user_id=scope.user_id, scope=scope)


@router.post("/bulk/restore", response_model=BulkResponse)
async def bulk_restore_teams(
    req: BulkIdsRequest,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.RESTORE)),
    service: TeamService = Depends(get_team_service),
) -> BulkResponse:
    """Restore multiple teams from trash."""
    return await service.bulk_restore(req=req, user_id=scope.user_id, scope=scope)


@router.delete("/bulk/permanent", response_model=BulkResponse)
@router.post("/bulk/permanent", response_model=BulkResponse, include_in_schema=False)
async def bulk_permanent_delete_teams(
    req: BulkIdsRequest,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.DELETE)),
    service: TeamService = Depends(get_team_service),
) -> BulkResponse:
    """Permanently delete multiple teams from trash."""
    return await service.bulk_permanent_delete(req=req, scope=scope)


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.READ)),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Get team details by ID."""
    return await service.get_team(team_id=team_id, scope=scope)


@router.patch("/{team_id}", response_model=TeamResponse)
async def update_team(
    team_id: uuid.UUID,
    data: TeamUpdate,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Update team metadata."""
    return await service.update_team(team_id=team_id, data=data, scope=scope)


@router.delete("/{team_id}", response_model=MessageResponse)
async def delete_team(
    team_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.DELETE)),
    service: TeamService = Depends(get_team_service),
) -> MessageResponse:
    """Delete a team."""
    await service.delete_team(team_id=team_id, scope=scope)
    return MessageResponse(message="Team deleted successfully")


@router.post("/{team_id}/restore", response_model=TeamResponse)
async def restore_team(
    team_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.RESTORE)),
    service: TeamService = Depends(get_team_service),
) -> TeamResponse:
    """Restore a single team from trash."""
    team = await service.restore(team_id, user_id=scope.user_id, scope=scope)
    return TeamResponse.model_validate(team)


# ==========================================
# Membership Endpoints
# ==========================================


@router.get("/{team_id}/members", response_model=list[TeamMemberResponse])
async def list_team_members(
    team_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.READ)),
    service: TeamService = Depends(get_team_service),
) -> list[TeamMemberResponse]:
    """List all members of a team."""
    return await service.list_members(team_id=team_id, scope=scope)


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_team_member(
    team_id: uuid.UUID,
    data: TeamMemberAdd,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> TeamMemberResponse:
    """Enroll a new member into the team."""
    return await service.add_member(team_id=team_id, data=data, scope=scope)


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberResponse)
async def update_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    data: TeamMemberUpdate,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> TeamMemberResponse:
    """Update role for a member inside the team."""
    return await service.update_member(
        team_id=team_id, user_id=user_id, data=data, scope=scope
    )


@router.delete("/{team_id}/members/{user_id}", response_model=MessageResponse)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> MessageResponse:
    """Remove member from team."""
    await service.remove_member(team_id=team_id, user_id=user_id, scope=scope)
    return MessageResponse(message="Member removed from team successfully")
