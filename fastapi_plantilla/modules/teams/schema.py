import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.mixins import RecordStatus

__all__ = [
    "TeamCreate",
    "TeamMemberAdd",
    "TeamMemberResponse",
    "TeamMemberUpdate",
    "TeamResponse",
    "TeamUpdate",
]


class TeamCreate(BaseModel):
    """Payload to create a new team."""

    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-_]+$")
    description: str | None = Field(default=None, max_length=255)


class TeamUpdate(BaseModel):
    """Payload to update team metadata."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    owner_id: uuid.UUID | None = None


class TeamMemberAdd(BaseModel):
    """Payload to add a user to a team."""

    user_id: uuid.UUID
    role_id: uuid.UUID | None = None


class TeamMemberUpdate(BaseModel):
    """Payload to update member's role inside a team."""

    role_id: uuid.UUID | None = None


class TeamMemberResponse(BaseModel):
    """Representation of a team member."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    user_email: str | None = None
    role_id: uuid.UUID | None = None
    role_slug: str | None = None
    created_at: datetime


class TeamResponse(BaseModel):
    """Team details response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    owner_id: uuid.UUID | None = None
    status: RecordStatus
    members_count: int = 0
    created_at: datetime
    updated_at: datetime
