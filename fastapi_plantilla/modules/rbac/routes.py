import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from fastapi_plantilla.core.crud.router import parse_if_match_version
from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    MessageResponse,
    PaginatedResponse,
)
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.dependencies import (
    get_rbac_service,
)
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
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> ModuleResponse:
    """Register a new system module (SuperAdmin only)."""
    return await service.create_module(data, user_id=current_user.id)


# ==========================================
# 2. Roles Management
# ==========================================


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    search: str | None = Query(default=None),
    name: str | None = Query(default=None),
    is_system: bool | None = Query(default=None),
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> list[RoleResponse]:
    """List all system roles with optional search (LIKE) and is_system filtering."""
    return await service.list_roles(search=search, name=name, is_system=is_system)


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


@router.post("/roles/bulk/trash", response_model=BulkResponse)
async def bulk_trash_roles(
    req: BulkIdsRequest,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> BulkResponse:
    """Move multiple non-system roles to trash (SuperAdmin only)."""
    return await service.bulk_trash(req=req, user_id=current_user.id)


@router.post("/roles/bulk/restore", response_model=BulkResponse)
async def bulk_restore_roles(
    req: BulkIdsRequest,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> BulkResponse:
    """Restore multiple roles from trash (SuperAdmin only)."""
    return await service.bulk_restore(req=req, user_id=current_user.id)


@router.delete("/roles/bulk/permanent", response_model=BulkResponse)
async def bulk_permanent_delete_roles(
    req: BulkIdsRequest,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> BulkResponse:
    """Permanently delete multiple roles from trash (SuperAdmin only)."""
    return await service.bulk_permanent_delete(req=req)


@router.get("/roles/{role_id}", response_model=RoleDetailResponse)
async def get_role(
    role_id: uuid.UUID,
    response: Response,
    _: UserResponse = Depends(get_current_user),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Get role details by ID with assigned permissions."""
    role = await service.get_role(role_id)
    response.headers["ETag"] = f'W/"{role.version}"'
    return role


@router.patch("/roles/{role_id}", response_model=RoleDetailResponse)
async def update_role(
    role_id: uuid.UUID,
    data: RoleUpdate,
    response: Response,
    expected_version: int | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Update role metadata (SuperAdmin only)."""
    parsed_version = parse_if_match_version(if_match)
    effective_version = (
        parsed_version if parsed_version is not None else expected_version
    )
    role = await service.update_role(
        role_id, data, user_id=current_user.id, expected_version=effective_version
    )
    response.headers["ETag"] = f'W/"{role.version}"'
    return role


@router.delete("/roles/{role_id}", response_model=RoleDetailResponse)
async def delete_role(
    role_id: uuid.UUID,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Delete a non-system role (SuperAdmin only)."""
    return await service.delete_role(role_id, user_id=current_user.id)


@router.post("/roles/{role_id}/restore", response_model=RoleDetailResponse)
async def restore_role(
    role_id: uuid.UUID,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Restore a role from trash (SuperAdmin only)."""
    role = await service.restore(role_id, user_id=current_user.id)
    return await service.get_role(role.id)


@router.put("/roles/{role_id}/permissions", response_model=RoleDetailResponse)
async def set_role_permissions(
    role_id: uuid.UUID,
    data: RolePermissionsUpdate,
    response: Response,
    expected_version: int | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleDetailResponse:
    """Replace all permissions for a role (SuperAdmin only)."""
    parsed_version = parse_if_match_version(if_match)
    effective_version = (
        parsed_version if parsed_version is not None else expected_version
    )
    role = await service.set_role_permissions(
        role_id,
        data.permissions,
        user_id=current_user.id,
        expected_version=effective_version,
    )
    response.headers["ETag"] = f'W/"{role.version}"'
    return role


@router.get(
    "/roles/{role_id}/assignments",
    response_model=PaginatedResponse[RoleAssignmentResponse],
)
async def list_role_assignments(
    role_id: uuid.UUID,
    params: Annotated[RoleAssignmentQueryParams, Depends()],
    _: UserResponse = Depends(get_current_active_superuser),
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
    _: UserResponse = Depends(get_current_active_superuser),
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
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> PaginatedResponse[RoleAssignmentResponse]:
    """List paginated role assignments with filtering and sorting."""
    return await service.list_assignments(params)


@router.get("/assignments/{assignment_id}", response_model=RoleAssignmentResponse)
async def get_assignment(
    assignment_id: uuid.UUID,
    _: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> RoleAssignmentResponse:
    """Get specific role assignment details by assignment ID."""
    return await service.get_assignment(assignment_id)


@router.post("/assignments", response_model=MessageResponse)
async def assign_role(
    data: RoleAssignmentRequest,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> MessageResponse:
    """Assign role to a user or a team (SuperAdmin only)."""
    await service.assign_role(
        data.role_id, data.entity_type, data.entity_id, user_id=current_user.id
    )
    return MessageResponse(message="Role assigned successfully")


@router.delete("/assignments", response_model=MessageResponse)
async def unassign_role(
    data: RoleAssignmentRequest,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: RbacService = Depends(get_rbac_service),
) -> MessageResponse:
    """Remove role assignment from a user or team (SuperAdmin only)."""
    await service.unassign_role(
        data.role_id, data.entity_type, data.entity_id, user_id=current_user.id
    )
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
