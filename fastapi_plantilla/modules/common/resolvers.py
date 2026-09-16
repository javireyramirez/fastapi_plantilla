"""Single Source of Truth (SSOT) for entity-to-model resolution across the system."""

from fastapi_plantilla.core.database import Base

__all__ = [
    "CODE_TO_ENTITY",
    "register_entity_model",
    "resolve_entity_model",
]

# Canonical mapping from system module plural codes to singular entity types
CODE_TO_ENTITY: dict[str, str] = {
    "companies": "company",
    "users": "user",
    "teams": "team",
    "roles": "role",
    "storage": "storage",
}

_ENTITY_REGISTRY: dict[str, type[Base]] = {}


def register_entity_model(entity_type: str, model: type[Base]) -> None:
    """Register custom entity model in the centralized registry."""
    _ENTITY_REGISTRY[entity_type.strip().lower()] = model


def resolve_entity_model(entity_type: str) -> type[Base] | None:
    """Resolve SQLAlchemy model class from registry or Base mappers."""
    norm = entity_type.strip().lower()
    if norm in _ENTITY_REGISTRY:
        return _ENTITY_REGISTRY[norm]

    # Map plural module codes to singular entity if present
    if norm in CODE_TO_ENTITY:
        mapped = CODE_TO_ENTITY[norm]
        if mapped in _ENTITY_REGISTRY:
            return _ENTITY_REGISTRY[mapped]

    # Inspect all registered SQLAlchemy mappers
    for mapper in Base.registry.mappers:
        cls: type[Base] = mapper.class_
        name = cls.__name__.lower()
        tbl = getattr(cls, "__tablename__", "").lower()

        # Check by class name, table name, or stripped sys_ prefix
        normalized_tbl = tbl.removeprefix("sys_").rstrip("s")
        if norm in (name, tbl, normalized_tbl):
            _ENTITY_REGISTRY[norm] = cls
            return cls

    return None
