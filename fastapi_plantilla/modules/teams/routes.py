import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from fastapi_plantilla.core.crud.schema import (
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
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.READ)),
    service: TeamService = Depends(get_team_service),
) -> PaginatedResponse[TeamResponse]:
    """List teams accessible within caller's scope."""
    return await service.list_teams(scope=scope, page=page, limit=limit)


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
