import uuid

from fastapi import Depends, Query

from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    MessageResponse,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.users.dependencies import get_user_admin_service
from fastapi_plantilla.modules.users.schema import (
    UserAdminCreate,
    UserAdminResponse,
    UserAdminUpdate,
    UserAssignRolesRequest,
    UserAssignTeamsRequest,
    UserBulkActionRequest,
    UserRemoveRolesRequest,
    UserRemoveTeamsRequest,
    UserRoleAssignmentResponse,
    UsersPaginationParams,
    UserTeamAssignmentResponse,
)
from fastapi_plantilla.modules.users.service import UserAdminService

router = create_crud_router(
    service_getter=get_user_admin_service,
    schema_out=UserAdminResponse,
    schema_create=UserAdminCreate,
    schema_update=UserAdminUpdate,
    prefix="/users",
    tags=["Users Admin"],
    resource_name="users",
    pagination_params=UsersPaginationParams,
)


@router.post("/bulk/suspend", response_model=BulkResponse)
async def bulk_suspend(
    data: UserBulkActionRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Suspend multiple users in bulk and terminate their sessions."""
    return await service.bulk_suspend(data.user_ids, user_id_actor=current_user.id)


@router.post("/bulk/reactivate", response_model=BulkResponse)
async def bulk_reactivate(
    data: UserBulkActionRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Reactivate multiple users in bulk."""
    return await service.bulk_reactivate(data.user_ids, user_id_actor=current_user.id)


@router.post("/bulk/activate", response_model=BulkResponse, include_in_schema=False)
async def bulk_activate(
    data: UserBulkActionRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Activate multiple users in bulk (alias for bulk_reactivate)."""
    return await service.bulk_reactivate(data.user_ids, user_id_actor=current_user.id)


@router.post("/{user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    """Suspend user and terminate all active sessions."""
    await service.suspend_user(user_id, user_id_actor=current_user.id)
    return MessageResponse(message="User suspended and active sessions invalidated")


@router.post("/{user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    """Reactivate a suspended user."""
    await service.reactivate_user(user_id, user_id_actor=current_user.id)
    return MessageResponse(message="User reactivated successfully")


@router.post(
    "/{user_id}/activate", response_model=MessageResponse, include_in_schema=False
)
async def activate_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
    current_user: UserResponse = Depends(get_current_user),
) -> MessageResponse:
    """Activate a suspended user (alias for reactivate)."""
    await service.reactivate_user(user_id, user_id_actor=current_user.id)
    return MessageResponse(message="User reactivated successfully")


@router.get(
    "/{user_id}/teams",
    response_model=PaginatedResponse[UserTeamAssignmentResponse],
)
async def get_user_teams(
    user_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: ScopeContext = Depends(require_permission("teams", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> PaginatedResponse[UserTeamAssignmentResponse]:
    """Fetch paginated teams assigned to user."""
    return await service.get_user_teams(user_id=user_id, page=page, limit=limit)


@router.post("/{user_id}/teams", response_model=BulkResponse)
async def assign_user_teams(
    user_id: uuid.UUID,
    data: UserAssignTeamsRequest,
    _: ScopeContext = Depends(require_permission("teams", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> BulkResponse:
    """Assign multiple teams to user in bulk."""
    return await service.assign_teams(user_id=user_id, team_ids=data.team_ids)


@router.delete("/{user_id}/teams", response_model=BulkResponse)
async def remove_user_teams(
    user_id: uuid.UUID,
    data: UserRemoveTeamsRequest,
    _: ScopeContext = Depends(require_permission("teams", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> BulkResponse:
    """Remove multiple teams from user in bulk."""
    return await service.remove_teams(user_id=user_id, team_ids=data.team_ids)


@router.get(
    "/{user_id}/roles",
    response_model=PaginatedResponse[UserRoleAssignmentResponse],
)
async def get_user_roles(
    user_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: ScopeContext = Depends(require_permission("roles", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> PaginatedResponse[UserRoleAssignmentResponse]:
    """Fetch paginated roles assigned to user with metadata."""
    return await service.get_user_roles_detailed(
        user_id=user_id, page=page, limit=limit
    )


@router.post("/{user_id}/roles", response_model=UserAdminResponse)
async def assign_user_roles(
    user_id: uuid.UUID,
    data: UserAssignRolesRequest,
    _: ScopeContext = Depends(require_permission("roles", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Assign security roles to user."""
    return await service.assign_roles(user_id, data.role_ids)


@router.delete("/{user_id}/roles", response_model=BulkResponse)
async def remove_user_roles_bulk(
    user_id: uuid.UUID,
    data: UserRemoveRolesRequest,
    _: ScopeContext = Depends(require_permission("roles", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> BulkResponse:
    """Remove multiple roles from user in bulk."""
    return await service.remove_roles_bulk(user_id=user_id, role_ids=data.role_ids)


@router.delete("/{user_id}/roles/{role_id}", response_model=UserAdminResponse)
async def remove_user_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("roles", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Remove a security role from user."""
    return await service.remove_role(user_id, role_id)


@router.post("/{user_id}/resend-invitation", response_model=MessageResponse)
async def resend_invitation(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> MessageResponse:
    """Resend email verification or invitation."""
    await service.resend_invitation(user_id)
    return MessageResponse(message="Invitation email resent successfully")
