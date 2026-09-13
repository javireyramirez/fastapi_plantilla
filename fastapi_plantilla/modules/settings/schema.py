import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SettingCreate",
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


class SettingCreate(BaseModel):
    """Payload to register a new system setting."""

    key: str = Field(..., min_length=1, max_length=100)
    value: Any = None
    description: str | None = Field(default=None, max_length=255)
    category: str = Field(default="general", max_length=50)
    is_public: bool = False


class SettingUpdate(BaseModel):
    """Payload to update setting value and metadata."""

    value: Any = None
    description: str | None = Field(default=None, max_length=255)
    is_public: bool | None = None
