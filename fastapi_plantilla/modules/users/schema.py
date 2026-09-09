import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from fastapi_plantilla.core.crud.schema import PaginationParams

__all__ = [
    "UserAdminCreate",
    "UserAdminResponse",
    "UserAdminUpdate",
    "UserAssignRolesRequest",
    "UserAssignTeamsRequest",
    "UserBulkActionRequest",
    "UserRemoveRolesRequest",
    "UserRemoveTeamsRequest",
    "UserRoleAssignmentResponse",
    "UserTeamAssignmentResponse",
    "UsersPaginationParams",
]


class UserAdminResponse(BaseModel):
    """Admin representation of a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    email_verified: bool
    is_active: bool
    is_super_admin: bool
    is_system: bool
    roles: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UserAdminCreate(BaseModel):
    """Payload for administrative user creation."""

    name: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    is_active: bool = True
    is_super_admin: bool = False
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class UserAdminUpdate(BaseModel):
    """Payload for administrative user update."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: EmailStr | None = None
    is_active: bool | None = None
    is_super_admin: bool | None = None


class UserBulkActionRequest(BaseModel):
    """Payload for bulk operations on users."""

    user_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=1000)


class UserAssignRolesRequest(BaseModel):
    """Payload to assign a set of roles to a user."""

    role_ids: list[uuid.UUID] = Field(..., min_length=1)


class UserRemoveRolesRequest(BaseModel):
    """Payload to remove a set of roles from a user in bulk."""

    role_ids: list[uuid.UUID] = Field(..., min_length=1)


class UserAssignTeamsRequest(BaseModel):
    """Payload to assign a set of teams to a user."""

    team_ids: list[uuid.UUID] = Field(..., min_length=1)


class UserRemoveTeamsRequest(BaseModel):
    """Payload to remove a set of teams from a user."""

    team_ids: list[uuid.UUID] = Field(..., min_length=1)


class UserTeamAssignmentResponse(BaseModel):
    """Representation of a team assigned to a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    role_id: uuid.UUID | None = None
    joined_at: datetime


class UserRoleAssignmentResponse(BaseModel):
    """Representation of a role assigned to a user."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    assigned_at: datetime


class UsersPaginationParams(PaginationParams):
    """Query parameters for user administration listing."""

    is_active: bool | None = None
    is_super_admin: bool | None = None
    email_verified: bool | None = None
