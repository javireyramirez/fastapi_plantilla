import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import ScopeType

__all__ = [
    "ModuleCreate",
    "ModuleResponse",
    "ModuleUpdate",
    "RbacActions",
    "RoleAssignmentRequest",
    "RoleAssignmentResponse",
    "RoleCreate",
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
    is_active: bool = True


class ModuleUpdate(BaseModel):
    """Payload to update an existing system module."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None


class ModuleResponse(BaseModel):
    """System module response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


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
    permissions: list[RolePermissionItem] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    """Payload to update role metadata."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    slug: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)


class RoleResponse(BaseModel):
    """Security role response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    is_system: bool
    created_at: datetime
    updated_at: datetime
    permissions: list[RolePermissionItem] = Field(default_factory=list)


class RoleAssignmentRequest(BaseModel):
    """Payload to assign a role to a user or a team."""

    role_id: uuid.UUID
    entity_type: str = Field(..., min_length=2, max_length=50)  # USER or TEAM
    entity_id: uuid.UUID


class RoleAssignmentResponse(BaseModel):
    """Representation of an active role assignment."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role_id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    created_at: datetime


class UserPermissionsMatrixResponse(BaseModel):
    """Effective user permissions across all modules and actions."""

    user_id: uuid.UUID
    is_super_admin: bool
    roles: list[str] = Field(default_factory=list)
    permissions: dict[str, dict[str, str]] = Field(default_factory=dict)
