import uuid

from fastapi import Depends, status

from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.core.crud.schema import (
    MessageResponse,
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

__all__ = ["router"]

router = create_crud_router(
    service_getter=get_team_service,
    schema_out=TeamResponse,
    schema_create=TeamCreate,
    schema_update=TeamUpdate,
    prefix="/teams",
    tags=["Teams"],
    resource_name="teams",
)


# ==========================================
# Membership Sub-resource Endpoints
# ==========================================


@router.get(
    "/{team_id}/members",
    response_model=list[TeamMemberResponse],
    summary="List all team members",
)
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
    summary="Enroll member into team",
)
async def add_team_member(
    team_id: uuid.UUID,
    data: TeamMemberAdd,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> TeamMemberResponse:
    """Enroll a new member into the team."""
    return await service.add_member(team_id=team_id, data=data, scope=scope)


@router.patch(
    "/{team_id}/members/{user_id}",
    response_model=TeamMemberResponse,
    summary="Update member role in team",
)
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


@router.delete(
    "/{team_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove member from team",
)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("teams", RbacActions.UPDATE)),
    service: TeamService = Depends(get_team_service),
) -> MessageResponse:
    """Remove member from team."""
    await service.remove_member(team_id=team_id, user_id=user_id, scope=scope)
    return MessageResponse(message="Member removed from team successfully")
