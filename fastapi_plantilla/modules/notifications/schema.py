import uuid
from datetime import datetime
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import PaginationParams
from fastapi_plantilla.modules.notifications.models import NotificationType

__all__ = [
    "MarkAllReadResponse",
    "NotificationCreate",
    "NotificationFanOutPayload",
    "NotificationFilterParams",
    "NotificationResponse",
    "UnreadCountResponse",
]


class NotificationResponse(BaseModel):
    """Public representation of an in-app notification."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    recipient_id: uuid.UUID
    title: str
    message: str
    notification_type: NotificationType = Field(alias="type")
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    action_url: str | None = None
    read_at: datetime | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_read(self) -> bool:
        """Indicate whether the notification has been marked as read."""
        return self.read_at is not None


class NotificationCreate(BaseModel):
    """Schema to programmatically create a notification."""

    model_config = ConfigDict(populate_by_name=True)

    recipient_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=150)
    message: str = Field(..., min_length=1)
    notification_type: NotificationType = Field(
        default=NotificationType.INFO, alias="type"
    )
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    action_url: str | None = Field(default=None, max_length=255)
    data: dict[str, Any] = Field(default_factory=dict)


class NotificationFilterParams(PaginationParams):
    """Query filters for user notification listing."""

    unread_only: bool = False
    type: list[NotificationType] | None = None

    @field_validator("type", mode="before")
    @classmethod
    def parse_notification_type(cls, v: Any) -> list[NotificationType] | None:
        """Parse singular or comma-separated notification types."""
        if not v:
            return None
        items = [v] if isinstance(v, (str, NotificationType)) else list(v)
        tokens: list[NotificationType] = []
        for it in items:
            if isinstance(it, NotificationType):
                tokens.append(it)
            elif isinstance(it, str):
                for part in it.split(","):
                    val = part.strip().upper()
                    if val:
                        tokens.append(NotificationType(val))
        return tokens or None


class UnreadCountResponse(BaseModel):
    """Response model for unread notification count badge."""

    unread_count: int


class MarkAllReadResponse(BaseModel):
    """Response model for bulk read action."""

    marked_count: int


class NotificationFanOutPayload(BaseModel):
    """Payload for notifications.fan_out background job with anti-DoS bounds."""

    model_config = ConfigDict(populate_by_name=True)

    user_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=settings.notifications_fan_out_max_recipients
    )
    title: str = Field(..., min_length=1, max_length=150)
    message: str = Field(..., min_length=1)
    notification_type: NotificationType = Field(
        default=NotificationType.INFO, alias="type"
    )
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    action_url: str | None = Field(default=None, max_length=255)
    data: dict[str, Any] = Field(default_factory=dict)
