import uuid

from fastapi import APIRouter, Depends, Query, status

from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    MessageResponse,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.users.dependencies import get_user_admin_service
from fastapi_plantilla.modules.users.schema import (
    UserAdminCreate,
    UserAdminResponse,
    UserAdminUpdate,
    UserAssignRolesRequest,
    UserBulkActionRequest,
)
from fastapi_plantilla.modules.users.service import UserAdminService

router = APIRouter(prefix="/users", tags=["Users Admin"])


@router.get("", response_model=PaginatedResponse[UserAdminResponse])
async def list_users(
    search: str | None = Query(default=None, max_length=100),
    is_active: bool | None = Query(default=None),
    is_super_admin: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    _: ScopeContext = Depends(require_permission("users", RbacActions.READ)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> PaginatedResponse[UserAdminResponse]:
    """List users paginated with optional search and state filters."""
    return await service.list_users(
        search=search,
        is_active=is_active,
        is_super_admin=is_super_admin,
        page=page,
        limit=limit,
    )


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    data: UserAdminCreate,
    scope: ScopeContext = Depends(require_permission("users", RbacActions.CREATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Administratively create a new user account."""
    return await service.create_user(data, actor_id=scope.user_id)


@router.post("/bulk/suspend", response_model=BulkResponse)
async def bulk_suspend(
    data: UserBulkActionRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> BulkResponse:
    """Suspend multiple users in bulk and terminate their sessions."""
    return await service.bulk_suspend(data.user_ids)


@router.post("/bulk/reactivate", response_model=BulkResponse)
async def bulk_reactivate(
    data: UserBulkActionRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> BulkResponse:
    """Reactivate multiple users in bulk."""
    return await service.bulk_reactivate(data.user_ids)


@router.get("/{user_id}", response_model=UserAdminResponse)
async def get_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.READ)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Fetch user administrative details."""
    return await service.get_user(user_id)


@router.patch("/{user_id}", response_model=UserAdminResponse)
async def update_user(
    user_id: uuid.UUID,
    data: UserAdminUpdate,
    scope: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Update user properties."""
    return await service.update_user(user_id, data, actor_id=scope.user_id)


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: uuid.UUID,
    scope: ScopeContext = Depends(require_permission("users", RbacActions.DELETE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> MessageResponse:
    """Soft-delete a user into trash."""
    await service.delete_user(user_id, actor_id=scope.user_id)
    return MessageResponse(message="User deleted successfully")


@router.post("/{user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> MessageResponse:
    """Suspend user and terminate all active sessions."""
    await service.suspend_user(user_id)
    return MessageResponse(message="User suspended and active sessions invalidated")


@router.post("/{user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    user_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.UPDATE)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> MessageResponse:
    """Reactivate a suspended user."""
    await service.reactivate_user(user_id)
    return MessageResponse(message="User reactivated successfully")


@router.post("/{user_id}/roles", response_model=UserAdminResponse)
async def assign_user_roles(
    user_id: uuid.UUID,
    data: UserAssignRolesRequest,
    _: ScopeContext = Depends(require_permission("users", RbacActions.SETTINGS)),
    service: UserAdminService = Depends(get_user_admin_service),
) -> UserAdminResponse:
    """Assign security roles to user."""
    return await service.assign_roles(user_id, data.role_ids)


@router.delete("/{user_id}/roles/{role_id}", response_model=UserAdminResponse)
async def remove_user_role(
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    _: ScopeContext = Depends(require_permission("users", RbacActions.SETTINGS)),
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
