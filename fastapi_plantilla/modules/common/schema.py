import enum
import uuid

from pydantic import BaseModel, ConfigDict

__all__ = ["EntityType", "PrincipalEntityModule"]


class EntityType(enum.StrEnum):
    """Canonical entity types for polymorphic associations across modules."""

    COMPANY = "company"
    USER = "user"
    TEAM = "team"
    ROLE = "role"
    STORAGE = "storage"


class PrincipalEntityModule(BaseModel):
    """Metadata of the parent entity to which a storage item belongs."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    code: str
    name: str
    entity_name: str | None = None
    entity_id: uuid.UUID | None = None
