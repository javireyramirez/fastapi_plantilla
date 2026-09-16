import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fastapi_plantilla.core.crud.schema import PaginationParams

__all__ = [
    "SessionAdminResponse",
    "SessionPaginationParams",
]


class SessionPaginationParams(PaginationParams):
    """Query parameters for paginated sessions list."""

    user_id: uuid.UUID | None = None
    is_valid: bool | None = None
    expires_at_from: datetime | None = None
    expires_at_to: datetime | None = None


class SessionAdminResponse(BaseModel):
    """Admin representation of a user session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    user_email: str
    ip_address: str | None = None
    user_agent: str | None = None
    is_valid: bool
    impersonated_by: uuid.UUID | None = None
    is_impersonated: bool = False
    created_at: datetime
    expires_at: datetime
    is_current: bool = False
