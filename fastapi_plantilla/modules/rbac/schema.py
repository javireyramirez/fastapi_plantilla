import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import ScopeType

__all__ = [
    "AssignedRoleBasic",
    "AssignedTeamBasic",
    "AssignedUserBasic",
    "ModuleCreate",
    "ModuleResponse",
    "ModuleUpdate",
    "RbacActions",
    "RoleAssignmentQueryParams",
    "RoleAssignmentRequest",
    "RoleAssignmentResponse",
    "RoleCreate",
    "RoleDetailResponse",
    "RolePermissionItem",
    "RolePermissionResponse",
    "RolePermissionsUpdate",
    "RoleResponse",
    "RoleUpdate",
    "UserPermissionsMatrixResponse",
]


class RbacActions(StrEnum):
    """Enumeration of system RBAC actions."""

    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    RESTORE = "RESTORE"
    EXPORT = "EXPORT"
    IMPORT = "IMPORT"
    SETTINGS = "SETTINGS"


class ModuleCreate(BaseModel):
    """Payload to register a new functional system module."""

    code: str = Field(..., min_length=2, max_length=50)
    name: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    category: str = Field(default="system", max_length=50)
    category_name: str | None = Field(default=None, max_length=100)
    category_icon: str | None = Field(default=None, max_length=50)
    category_order: int = 0
    icon: str | None = Field(default=None, max_length=50)
    sort_order: int = 0
    is_active: bool = True
    is_trasheable: bool = True


class ModuleUpdate(BaseModel):
    """Payload to update an existing system module."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=50)
    category_name: str | None = Field(default=None, max_length=100)
    category_icon: str | None = Field(default=None, max_length=50)
    category_order: int | None = None
    icon: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None
    is_active: bool | None = None
    is_trasheable: bool | None = None


class ModuleResponse(BaseModel):
    """System module response representation matching catalog JSON."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    category: str = "system"
    category_name: str | None = None
    category_icon: str | None = None
    category_order: int = 0
    icon: str | None = None
    sort_order: int = 0
    is_active: bool
    is_trasheable: bool = True


class RolePermissionItem(BaseModel):
    """Single permission definition associating module, action, and scope."""

    model_config = ConfigDict(from_attributes=True)

    module_code: str
    action: RbacActions
    scope: ScopeType = ScopeType.OWN


class RolePermissionResponse(BaseModel):
    """Representation of an assigned role permission."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role_id: uuid.UUID
    module_id: uuid.UUID
    action: RbacActions
    scope: ScopeType


class RolePermissionsUpdate(BaseModel):
    """Payload to replace all permissions associated with a role."""

    permissions: list[RolePermissionItem]


class RoleCreate(BaseModel):
    """Payload to create a new security role."""

    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    color: str | None = Field(default=None, max_length=50)
    icon: str | None = Field(default=None, max_length=50)
    permissions: list[RolePermissionItem] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Payload to update role metadata."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    slug: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    color: str | None = Field(default=None, max_length=50)
    icon: str | None = Field(default=None, max_length=50)


class RoleResponse(BaseModel):
    """Security role response representation without permissions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    is_system: bool
    created_at: datetime
    updated_at: datetime


class RoleDetailResponse(RoleResponse):
    """Security role detailed response including full assigned permissions."""

    permissions: list[RolePermissionItem] = Field(default_factory=list)


class RoleAssignmentRequest(BaseModel):
    """Payload to assign a role to a user or a team."""

    role_id: uuid.UUID = Field(..., validation_alias=AliasChoices("role_id", "roleId"))
    entity_type: str = Field(
        ...,
        min_length=2,
        max_length=50,
        validation_alias=AliasChoices("entity_type", "entityType"),
    )  # USER or TEAM
    entity_id: uuid.UUID = Field(
        ..., validation_alias=AliasChoices("entity_id", "entityId")
    )


class AssignedRoleBasic(BaseModel):
    """Basic role summary for assignment payload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class AssignedUserBasic(BaseModel):
    """Basic user summary for assignment payload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None = None
    email: str | None = None


class AssignedTeamBasic(BaseModel):
    """Basic team summary for assignment payload."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str | None = None


class RoleAssignmentQueryParams(BaseModel):
    """Query filters for retrieving paginated role assignments."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=10, ge=1, le=100)
    sort_by: str = Field(
        default="assignedAt",
        validation_alias=AliasChoices("sort_by", "sortBy"),
    )
    sort_order: str = Field(
        default="desc",
        validation_alias=AliasChoices("sort_order", "sortOrder"),
    )
    role_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("role_id", "roleId"),
    )
    user_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("user_id", "userId"),
    )
    team_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("team_id", "teamId"),
    )
    entity_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("entity_type", "entityType"),
    )
    assigned_from: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "assigned_from", "assignedFrom", "created_at_from", "createdAtFrom"
        ),
    )
    assigned_to: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "assigned_to", "assignedTo", "created_at_to", "createdAtTo"
        ),
    )


class RoleAssignmentResponse(BaseModel):
    """Representation of an active role assignment."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    role_id: uuid.UUID = Field(..., validation_alias=AliasChoices("role_id", "roleId"))
    roleId: uuid.UUID | None = Field(default=None)  # noqa: N815
    entity_type: str = Field(
        ..., validation_alias=AliasChoices("entity_type", "entityType")
    )
    entityType: str | None = Field(default=None)  # noqa: N815
    entity_id: uuid.UUID = Field(
        ..., validation_alias=AliasChoices("entity_id", "entityId")
    )
    entityId: uuid.UUID | None = Field(default=None)  # noqa: N815
    created_at: datetime = Field(
        ...,
        validation_alias=AliasChoices(
            "created_at", "createdAt", "assigned_at", "assignedAt"
        ),
    )
    assigned_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "assigned_at", "assignedAt", "created_at", "createdAt"
        ),
    )
    assignedAt: datetime | None = Field(default=None)  # noqa: N815
    user_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasChoices("user_id", "userId")
    )
    userId: uuid.UUID | None = Field(default=None)  # noqa: N815
    team_id: uuid.UUID | None = Field(
        default=None, validation_alias=AliasChoices("team_id", "teamId")
    )
    teamId: uuid.UUID | None = Field(default=None)  # noqa: N815

    role: AssignedRoleBasic | None = None
    user: AssignedUserBasic | None = None
    assigned_user: AssignedUserBasic | None = None
    assignedUser: AssignedUserBasic | None = Field(default=None)  # noqa: N815
    team: AssignedTeamBasic | None = None
    assigned_team: AssignedTeamBasic | None = None
    assignedTeam: AssignedTeamBasic | None = Field(default=None)  # noqa: N815


class UserPermissionsMatrixResponse(BaseModel):
    """Effective user permissions across all modules and actions."""

    user_id: uuid.UUID
    is_super_admin: bool
    roles: list[str] = Field(default_factory=list)
    permissions: dict[str, dict[str, str]] = Field(default_factory=dict)
