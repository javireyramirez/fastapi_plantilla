import uuid
from typing import Any

from fastapi import Depends, HTTPException, status

from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    PaginationMeta,
    ScopeType,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import Role, RoleAssignment
from fastapi_plantilla.modules.rbac.repository import RbacRepository
from fastapi_plantilla.modules.rbac.schema import (
    AssignedRoleBasic,
    AssignedTeamBasic,
    AssignedUserBasic,
    ModuleCreate,
    ModuleResponse,
    RbacActions,
    RoleAssignmentQueryParams,
    RoleAssignmentResponse,
    RoleCreate,
    RolePermissionItem,
    RoleResponse,
    RoleUpdate,
    UserPermissionsMatrixResponse,
)
from fastapi_plantilla.modules.teams.models import Team

__all__ = ["SCOPE_PRIORITY", "RbacService"]

SCOPE_PRIORITY: dict[str, int] = {
    ScopeType.OWN: 1,
    ScopeType.TEAM: 2,
    ScopeType.GLOBAL: 3,
}


class RbacService(BaseAuditService[Role]):
    """Business logic service for RBAC governance and scope evaluation."""

    resource_name: str = "Role"
    display_field: str = "name"

    def __init__(self, repository: RbacRepository = Depends()) -> None:
        super().__init__(repository)
        self.repository: RbacRepository = repository

    async def list_modules(self) -> list[ModuleResponse]:
        """List all system modules."""
        modules = await self.repository.list_modules()
        return [ModuleResponse.model_validate(m) for m in modules]

    async def create_module(self, data: ModuleCreate) -> ModuleResponse:
        """Register a new system module ensuring unique code."""
        existing = await self.repository.get_module_by_code(data.code)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Module with code '{data.code}' already exists",
            )
        module = await self.repository.create_module(
            code=data.code,
            name=data.name,
            description=data.description,
            is_active=data.is_active,
        )
        return ModuleResponse.model_validate(module)

    async def _get_role_or_404(
        self, role_id: uuid.UUID, load_permissions: bool = True
    ) -> Role:
        """Fetch active role or raise 404."""
        role = await self.repository.get_role_by_id(
            role_id, load_permissions=load_permissions
        )
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
            )
        return role

    async def list_roles(self) -> list[RoleResponse]:
        """List all roles with their assigned permissions."""
        roles = await self.repository.list_roles()
        return [RoleResponse.model_validate(r) for r in roles]

    async def get_role(self, role_id: uuid.UUID) -> RoleResponse:
        """Get role by ID."""
        role = await self._get_role_or_404(role_id)
        return RoleResponse.model_validate(role)

    async def create_role(
        self, data: RoleCreate, user_id: uuid.UUID | None = None
    ) -> RoleResponse:
        """Create new role and optionally attach initial permissions."""
        existing = await self.repository.get_role_by_slug(data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Role with slug '{data.slug}' already exists",
            )
        payload = data.model_dump(include={"name", "slug", "description"})
        payload["is_system"] = False
        role = await self.create(payload, user_id=user_id)
        if data.permissions:
            await self.set_role_permissions(role.id, data.permissions)
            role = await self._get_role_or_404(role.id)

        return RoleResponse.model_validate(role)

    async def update_role(
        self,
        role_id: uuid.UUID,
        data: RoleUpdate,
        user_id: uuid.UUID | None = None,
    ) -> RoleResponse:
        """Update role name and description."""
        role = await self._get_role_or_404(role_id)
        if data.slug is not None and data.slug != role.slug and role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot change slug of a system role",
            )
        update_dict = data.model_dump(exclude_unset=True)
        if update_dict:
            await self.update(role_id, update_dict, user_id=user_id)
        refreshed = await self._get_role_or_404(role_id)
        return RoleResponse.model_validate(refreshed)

    async def delete_role(
        self, role_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> None:
        """Delete role (soft delete into trash) preventing removal of system roles."""
        role = await self._get_role_or_404(role_id)
        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="System roles cannot be deleted",
            )
        await self.trash(role_id, user_id=user_id)

    async def set_role_permissions(
        self, role_id: uuid.UUID, permissions: list[RolePermissionItem]
    ) -> RoleResponse:
        """Replace all permissions for a role."""
        await self._get_role_or_404(role_id, load_permissions=False)
        items: list[dict[str, Any]] = []
        for perm in permissions:
            module = await self.repository.get_module_by_code(perm.module_code)
            if not module:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"System module '{perm.module_code}' not found",
                )
            items.append(
                {"module_id": module.id, "action": perm.action, "scope": perm.scope}
            )

        await self.repository.set_role_permissions(role_id, items)
        refreshed = await self._get_role_or_404(role_id)
        return RoleResponse.model_validate(refreshed)

    async def assign_role(
        self, role_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
    ) -> None:
        """Assign role to user or team."""
        await self._get_role_or_404(role_id, load_permissions=False)
        await self.repository.assign_role(role_id, entity_type, entity_id)

    async def unassign_role(
        self, role_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
    ) -> None:
        """Remove role assignment from user or team."""
        await self.repository.unassign_role(role_id, entity_type, entity_id)

    def _serialize_assignment_row(
        self, row: tuple[RoleAssignment, Role, User | None, Team | None]
    ) -> RoleAssignmentResponse:
        assignment, role, user, team = row
        is_user = assignment.entity_type.upper() == "USER"
        is_team = assignment.entity_type.upper() == "TEAM"

        user_basic = None
        if is_user and user:
            user_basic = AssignedUserBasic(id=user.id, name=user.name, email=user.email)

        team_basic = None
        if is_team and team:
            team_basic = AssignedTeamBasic(id=team.id, name=team.name, slug=team.slug)

        role_basic = AssignedRoleBasic(id=role.id, name=role.name, slug=role.slug)

        return RoleAssignmentResponse(
            id=assignment.id,
            role_id=assignment.role_id,
            roleId=assignment.role_id,
            entity_type=assignment.entity_type.lower(),
            entityType=assignment.entity_type.lower(),
            entity_id=assignment.entity_id,
            entityId=assignment.entity_id,
            created_at=assignment.created_at,
            assigned_at=assignment.created_at,
            assignedAt=assignment.created_at,
            user_id=assignment.entity_id if is_user else None,
            userId=assignment.entity_id if is_user else None,
            team_id=assignment.entity_id if is_team else None,
            teamId=assignment.entity_id if is_team else None,
            role=role_basic,
            user=user_basic,
            assigned_user=user_basic,
            assignedUser=user_basic,
            team=team_basic,
            assigned_team=team_basic,
            assignedTeam=team_basic,
        )

    async def list_assignments(
        self, params: RoleAssignmentQueryParams
    ) -> PaginatedResponse[RoleAssignmentResponse]:
        """List paginated role assignments filtered by role, user, or team."""
        if params.role_id:
            await self._get_role_or_404(params.role_id, load_permissions=False)

        rows, total = await self.repository.list_assignments(params)
        data = [self._serialize_assignment_row(row) for row in rows]
        return PaginatedResponse(
            data=data,
            meta=PaginationMeta.create(
                page=params.page, limit=params.limit, total=total
            ),
        )

    async def get_assignment(
        self, assignment_id: uuid.UUID, role_id: uuid.UUID | None = None
    ) -> RoleAssignmentResponse:
        """Get single role assignment by ID with role, user, and team details."""
        if role_id:
            await self._get_role_or_404(role_id, load_permissions=False)

        row = await self.repository.get_assignment_by_id(assignment_id, role_id)
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role assignment not found",
            )
        return self._serialize_assignment_row(row)

    async def resolve_user_permission(
        self,
        user_id: uuid.UUID,
        module_code: str,
        action: RbacActions,
        is_super_admin: bool = False,
    ) -> tuple[ScopeType | None, list[uuid.UUID], list[uuid.UUID]]:
        """Evaluate user permissions for specific module and action."""
        (
            _,
            perms,
            team_ids,
            teammate_ids,
        ) = await self.repository.get_user_effective_permissions(user_id)
        if is_super_admin:
            return ScopeType.GLOBAL, team_ids, teammate_ids

        matching_scopes = [
            ScopeType(scope)
            for m_code, act, scope in perms
            if m_code == module_code and act == action.value
        ]
        if not matching_scopes:
            return None, team_ids, teammate_ids

        highest_scope = max(matching_scopes, key=lambda s: SCOPE_PRIORITY.get(s, 0))
        return highest_scope, team_ids, teammate_ids

    async def get_user_permissions_matrix(
        self, user_id: uuid.UUID, is_super_admin: bool = False
    ) -> UserPermissionsMatrixResponse:
        """Build full effective permissions matrix for client inspection."""
        role_slugs, perms, _, _ = await self.repository.get_user_effective_permissions(
            user_id
        )
        matrix: dict[str, dict[str, str]] = {}
        for m_code, act, scope in perms:
            mod_map = matrix.setdefault(m_code, {})
            current = mod_map.get(act)
            if current is None or (
                SCOPE_PRIORITY.get(scope, 0) > SCOPE_PRIORITY.get(current, 0)
            ):
                mod_map[act] = scope

        return UserPermissionsMatrixResponse(
            user_id=user_id,
            is_super_admin=is_super_admin,
            roles=role_slugs,
            permissions=matrix,
        )
