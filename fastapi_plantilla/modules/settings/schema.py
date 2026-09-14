import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SettingResponse",
    "SettingUpdate",
]


class SettingResponse(BaseModel):
    """System setting response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    value: Any
    description: str | None = None
    category: str
    is_public: bool
    created_at: datetime
    updated_at: datetime


class SettingUpdate(BaseModel):
    """Payload to update setting value and metadata."""

    value: Any = None
    description: str | None = Field(default=None, max_length=255)
