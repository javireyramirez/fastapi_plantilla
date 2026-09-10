import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from fastapi_plantilla.core.crud.schema import MessageResponse, PaginatedResponse
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.dependencies import get_rbac_service
from fastapi_plantilla.modules.rbac.schema import (
    ModuleCreate,
    ModuleResponse,
    RoleAssignmentQueryParams,
    RoleAssignmentRequest,
    RoleAssignmentResponse,
    RoleCreate,
    RoleDetailResponse,
    RolePermissionsUpdate,
    RoleResponse,
    RoleUpdate,
    UserPermissionsMatrixResponse,
)
from fastapi_plantilla.modules.rbac.service import RbacService

router = APIRouter(prefix="/rbac", tags=["RBAC"])


# ==========================================
# 1. System Modules
# ==========================================


@router.get("/modules", response_model=list[ModuleResponse])
async def list_modules(
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> list[ModuleResponse]:
    """List all registered system modules."""
    return await service.list_modules()


@router.post(
    "/modules", response_model=ModuleResponse, status_code=status.HTTP_201_CREATED
)
async def create_module(
    data: ModuleCreate,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> ModuleResponse:
    """Register a new system module (SuperAdmin only)."""
    return await service.create_module(data)


# ==========================================
# 2. Roles Management
# ==========================================


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> list[RoleResponse]:
    """List all system roles with assigned permissions."""
    return await service.list_roles()


@router.post(
    "/roles", response_model=RoleDetailResponse, status_code=status.HTTP_201_CREATED
)
async def create_role(
    data: RoleCreate,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Create a new role with optional initial permissions (SuperAdmin only)."""
    return await service.create_role(data, user_id=current_user.id)


@router.get("/roles/{role_id}", response_model=RoleDetailResponse)
async def get_role(
    role_id: uuid.UUID,
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Get role details by ID with assigned permissions."""
    return await service.get_role(role_id)


@router.patch("/roles/{role_id}", response_model=RoleDetailResponse)
async def update_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Update role metadata (SuperAdmin only)."""
    return await service.update_role(role_id, data, user_id=current_user.id)


@router.delete("/roles/{role_id}", response_model=MessageResponse)
async def delete_role(
    role_id: uuid.UUID,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> MessageResponse:
    """Delete a non-system role (SuperAdmin only)."""
    await service.delete_role(role_id, user_id=current_user.id)
    return MessageResponse(message="Role deleted successfully")


@router.put("/roles/{role_id}/permissions", response_model=RoleDetailResponse)
async def set_role_permissions(
    role_id: uuid.UUID,
    data: RolePermissionsUpdate,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Replace all permissions for a role (SuperAdmin only)."""
    return await service.set_role_permissions(role_id, data.permissions)


@router.get(
    "/roles/{role_id}/assignments",
    response_model=PaginatedResponse[RoleAssignmentResponse],
)
async def list_role_assignments(
    role_id: uuid.UUID,
    params: Annotated[RoleAssignmentQueryParams, Depends()],
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> PaginatedResponse[RoleAssignmentResponse]:
    """List paginated assignments specifically for a given role."""
    params.role_id = role_id
    return await service.list_assignments(params)


@router.get(
    "/roles/{role_id}/assignments/{assignment_id}",
    response_model=RoleAssignmentResponse,
)
async def get_role_assignment(
    role_id: uuid.UUID,
    assignment_id: uuid.UUID,
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> RoleAssignmentResponse:
    """Get specific assignment details scoped to a given role."""
    return await service.get_assignment(assignment_id, role_id=role_id)


# ==========================================
# 3. Polymorphic Assignments & Effective Permissions
# ==========================================


@router.get("/assignments", response_model=PaginatedResponse[RoleAssignmentResponse])
async def list_assignments(
    params: Annotated[RoleAssignmentQueryParams, Depends()],
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> PaginatedResponse[RoleAssignmentResponse]:
    """List paginated role assignments with filtering and sorting."""
    return await service.list_assignments(params)


@router.get("/assignments/{assignment_id}", response_model=RoleAssignmentResponse)
async def get_assignment(
    assignment_id: uuid.UUID,
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> RoleAssignmentResponse:
    """Get specific role assignment details by assignment ID."""
    return await service.get_assignment(assignment_id)


@router.post("/assignments", response_model=MessageResponse)
async def assign_role(
    data: RoleAssignmentRequest,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> MessageResponse:
    """Assign role to a user or a team (SuperAdmin only)."""
    await service.assign_role(data.role_id, data.entity_type, data.entity_id)
    return MessageResponse(message="Role assigned successfully")


@router.delete("/assignments", response_model=MessageResponse)
async def unassign_role(
    data: RoleAssignmentRequest,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> MessageResponse:
    """Remove role assignment from a user or team (SuperAdmin only)."""
    await service.unassign_role(data.role_id, data.entity_type, data.entity_id)
    return MessageResponse(message="Role assignment removed successfully")


@router.get("/my-permissions", response_model=UserPermissionsMatrixResponse)
async def get_my_permissions(
    current_user: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> UserPermissionsMatrixResponse:
    """Retrieve full permissions matrix for authenticated user."""
    return await service.get_user_permissions_matrix(
        user_id=current_user.id,
        is_super_admin=current_user.is_super_admin,
    )
