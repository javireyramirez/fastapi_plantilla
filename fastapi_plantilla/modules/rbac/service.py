import uuid
from typing import Any

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

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
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.catalog import MODULE_CATEGORIES
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
    RoleDetailResponse,
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

    def _check_not_system(self, role: Role, action: str = "deleted") -> None:
        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"System roles cannot be {action}",
            )

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
        cat_meta = MODULE_CATEGORIES.get(data.category, {})
        cat_name = data.category_name or cat_meta.get("name")
        cat_icon = data.category_icon or cat_meta.get("icon")
        cat_order = (
            data.category_order
            if data.category_order != 0
            else int(cat_meta.get("order", 0))
        )
        module = await self.repository.create_module(
            code=data.code,
            name=data.name,
            description=data.description,
            category=data.category,
            category_name=cat_name,
            category_icon=cat_icon,
            category_order=cat_order,
            icon=data.icon,
            sort_order=data.sort_order,
            is_active=data.is_active,
            supported_actions=[
                a.value if hasattr(a, "value") else str(a)
                for a in data.supported_actions
            ],
            requires_super_admin=data.requires_super_admin,
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

    async def list_roles(
        self,
        search: str | None = None,
        name: str | None = None,
        is_system: bool | None = None,
    ) -> list[RoleResponse]:
        """List all roles with optional search (LIKE) and is_system filtering."""
        roles = await self.repository.list_roles(
            search=search, name=name, is_system=is_system, load_permissions=False
        )
        return [RoleResponse.model_validate(r) for r in roles]

    async def get_role(self, role_id: uuid.UUID) -> RoleDetailResponse:
        """Get role by ID with all assigned permissions."""
        role = await self._get_role_or_404(role_id, load_permissions=True)
        return RoleDetailResponse.model_validate(role)

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Role:
        """Create a role, preventing duplicate slugs with 409 conflict."""
        payload: dict[str, Any] = (
            data.model_dump() if isinstance(data, BaseModel) else dict(data)
        )
        slug = payload.get("slug")
        if slug:
            existing = await self.repository.get_role_by_slug(slug)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Role with slug '{slug}' already exists",
                )
        payload["is_system"] = False
        try:
            async with self.repository.session.begin_nested():
                return await super().create(
                    payload,
                    user_id=user_id,
                    owner_id=owner_id,
                    scope=scope,
                    allow_immutable=allow_immutable,
                    options=options,
                )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Role with slug '{slug}' already exists",
            ) from exc

    async def create_role(
        self, data: RoleCreate, user_id: uuid.UUID | None = None
    ) -> RoleDetailResponse:
        """Create new role and optionally attach initial permissions."""
        payload = data.model_dump(
            include={"name", "slug", "description", "color", "icon"}
        )
        try:
            async with self.repository.session.begin_nested():
                role = await self.create(payload, user_id=user_id)
                if data.permissions:
                    await self.set_role_permissions(
                        role.id, data.permissions, user_id=user_id
                    )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Role with slug '{data.slug}' already exists",
            ) from exc

        refreshed = await self._get_role_or_404(role.id, load_permissions=True)
        return RoleDetailResponse.model_validate(refreshed)

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
    ) -> Role:
        """Update role metadata, protecting system role slug and optimistic locking."""
        role = await self._get_role_or_404(id, load_permissions=False)
        update_dict: dict[str, Any] = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )

        body_version = update_dict.pop("version", None)
        version_to_check = (
            expected_version if expected_version is not None else body_version
        )

        if "slug" in update_dict and update_dict["slug"] is not None:
            new_slug = update_dict["slug"]
            if new_slug != role.slug:
                if role.is_system:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot change slug of a system role",
                    )
                existing = await self.repository.get_role_by_slug(new_slug)
                if existing and existing.id != id:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Role with slug '{new_slug}' already exists",
                    )

        try:
            async with self.repository.session.begin_nested():
                return await super().update(
                    id,
                    update_dict,
                    *where,
                    expected_version=version_to_check,
                    user_id=user_id,
                    scope=scope,
                    allow_immutable=allow_immutable,
                    options=options,
                )
        except IntegrityError as exc:
            slug_val = update_dict.get("slug", "")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Role with slug '{slug_val}' already exists",
            ) from exc

    async def update_role(
        self,
        role_id: uuid.UUID,
        data: RoleUpdate,
        user_id: uuid.UUID | None = None,
        expected_version: int | None = None,
    ) -> RoleDetailResponse:
        """Update role name and description."""
        await self.update(
            role_id, data, user_id=user_id, expected_version=expected_version
        )
        refreshed = await self._get_role_or_404(role_id, load_permissions=True)
        return RoleDetailResponse.model_validate(refreshed)

    async def trash(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Role:
        """Move role to trash, preventing removal of system roles."""
        role = await self._get_role_or_404(id, load_permissions=False)
        self._check_not_system(role, "deleted")
        return await super().trash(
            id, *where, user_id=user_id, scope=scope, options=options
        )

    async def permanent_delete(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Role:
        """Permanently delete role from trash, preventing removal of system roles."""
        role = await self.repository.get_role_by_id(id, load_permissions=False)
        if role and role.is_system:
            self._check_not_system(role, "deleted")
        return await super().permanent_delete(id, *where, scope=scope, options=options)

    async def delete_role(
        self, role_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> None:
        """Delete role (soft delete into trash) preventing removal of system roles."""
        await self.delete(role_id, user_id=user_id)

    async def bulk_trash(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Move multiple roles to trash, preventing deletion of system roles."""
        has_system = await self.repository.exists(
            self.repository.pk.in_(req.ids), Role.is_system.is_(True)
        )
        if has_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="System roles cannot be deleted",
            )
        return await super().bulk_trash(
            req, *where, user_id=user_id, scope=scope, options=options
        )

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Role:
        """Restore role ensuring no active role with the same slug exists."""
        role = await self.repository.find_first(Role.id == id)
        if role and role.status == RecordStatus.TRASHED:
            existing = await self.repository.get_role_by_slug(role.slug)
            if existing and existing.id != role.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore role '{role.name}': slug '{role.slug}' "
                        "is already in use by an active role."
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
        """Bulk restore roles ensuring no active roles conflict with their slugs."""
        roles = await self.repository.find_many(
            Role.id.in_(req.ids), Role.status == RecordStatus.TRASHED
        )
        for r in roles:
            existing = await self.repository.get_role_by_slug(r.slug)
            if existing and existing.id not in req.ids:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore role '{r.name}': slug '{r.slug}' "
                        "is already in use by an active role."
                    ),
                )
        return await super().bulk_restore(
            req, *where, user_id=user_id, scope=scope, options=options
        )

    async def bulk_permanent_delete(
        self,
        req: BulkIdsRequest,
        *where: Any,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Permanently delete roles from trash, preventing deletion of system roles."""
        has_system = await self.repository.exists(
            self.repository.pk.in_(req.ids), Role.is_system.is_(True)
        )
        if has_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="System roles cannot be deleted",
            )
        return await super().bulk_permanent_delete(
            req, *where, scope=scope, options=options
        )

    async def set_role_permissions(
        self,
        role_id: uuid.UUID,
        permissions: list[RolePermissionItem],
        user_id: uuid.UUID | None = None,
    ) -> RoleDetailResponse:
        """Replace all permissions for a role."""
        role = await self._get_role_or_404(role_id, load_permissions=False)
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

        async with self.repository.session.begin_nested():
            await self.repository.set_role_permissions(role_id, items)

        await self._emit_audit(
            item=role,
            action="SET_ROLE_PERMISSIONS",
            user_id=user_id,
            entity_id=role.id,
            entity_name=role.name,
            details=(
                f"Updated permissions for role {role.name} "
                f"({len(permissions)} permissions)"
            ),
        )

        refreshed = await self._get_role_or_404(role_id, load_permissions=True)
        return RoleDetailResponse.model_validate(refreshed)

    async def assign_role(
        self,
        role_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Assign role to user or team."""
        role = await self._get_role_or_404(role_id, load_permissions=False)
        norm_type = entity_type.strip().upper()
        if norm_type not in ("USER", "TEAM"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entity_type '{entity_type}'. Must be 'USER' or 'TEAM'",
            )

        if norm_type == "USER" and not await self.repository.user_exists(entity_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with ID {entity_id} not found",
            )
        if norm_type == "TEAM" and not await self.repository.team_exists(entity_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Team with ID {entity_id} not found",
            )

        assignment = await self.repository.assign_role(role_id, norm_type, entity_id)
        await self._emit_audit(
            item=assignment,
            action="ASSIGN_ROLE",
            user_id=user_id,
            entity_id=role.id,
            entity_name=role.name,
            details=f"Assigned role {role.name} to {norm_type.lower()} {entity_id}",
        )

    async def unassign_role(
        self,
        role_id: uuid.UUID,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Remove role assignment from user or team."""
        role = await self._get_role_or_404(role_id, load_permissions=False)
        norm_type = entity_type.strip().upper()
        removed = await self.repository.unassign_role(role_id, norm_type, entity_id)
        if removed:
            await self._emit_audit(
                item=role,
                action="UNASSIGN_ROLE",
                user_id=user_id,
                entity_id=role.id,
                entity_name=role.name,
                details=(
                    f"Unassigned role {role.name} from {norm_type.lower()} {entity_id}"
                ),
            )

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
            entity_type=assignment.entity_type.lower(),
            entity_id=assignment.entity_id,
            created_at=assignment.created_at,
            assigned_at=assignment.created_at,
            user_id=assignment.entity_id if is_user else None,
            team_id=assignment.entity_id if is_team else None,
            role=role_basic,
            user=user_basic,
            assigned_user=user_basic,
            team=team_basic,
            assigned_team=team_basic,
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
