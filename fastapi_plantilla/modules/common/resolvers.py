"""Single Source of Truth (SSOT) for entity-to-model resolution across the system."""

from fastapi_plantilla.core.database import Base

__all__ = [
    "CODE_TO_ENTITY",
    "ENTITY_TO_CODE",
    "MODULE_NAMES",
    "normalize_entity_types",
    "register_entity_model",
    "resolve_entity_model",
    "resolve_module_metadata",
]

# Canonical mapping from system module plural codes to singular entity types
CODE_TO_ENTITY: dict[str, str] = {
    "companies": "company",
    "users": "user",
    "teams": "team",
    "roles": "role",
    "storage": "storage",
    "auth": "user",
}

# Canonical reverse mapping from singular entity types to plural module codes
ENTITY_TO_CODE: dict[str, str] = {
    "company": "companies",
    "companies": "companies",
    "user": "users",
    "users": "users",
    "auth": "users",
    "team": "teams",
    "teams": "teams",
    "role": "roles",
    "roles": "roles",
    "storage": "storage",
}

# Human-readable Spanish display names for system modules
MODULE_NAMES: dict[str, str] = {
    "companies": "Compañías",
    "company": "Compañías",
    "users": "Usuarios",
    "user": "Usuarios",
    "auth": "Usuarios",
    "teams": "Equipos",
    "team": "Equipos",
    "roles": "Roles",
    "role": "Roles",
    "storage": "Almacenamiento",
}

_ENTITY_REGISTRY: dict[str, type[Base]] = {}


def register_entity_model(entity_type: str, model: type[Base]) -> None:
    """Register custom entity model in the centralized registry."""
    _ENTITY_REGISTRY[entity_type.strip().lower()] = model


def resolve_module_metadata(entity_type: str | None) -> tuple[str, str]:
    """Resolve canonical module code and human-readable name from entity_type."""
    if not entity_type:
        return ("unknown", "Desconocido")
    raw = entity_type.strip().lower()
    code = ENTITY_TO_CODE.get(raw, CODE_TO_ENTITY.get(raw, raw))
    name = MODULE_NAMES.get(raw, MODULE_NAMES.get(code))
    if name is not None:
        return (code, name)
    clean = raw.removesuffix("s") if raw.endswith("s") and len(raw) > 3 else raw
    return (raw, clean.replace("_", " ").title())


def normalize_entity_types(value: str | None) -> list[str]:
    """Split comma-separated filter into singular canonical entity types."""
    if not value:
        return []
    raw_types = [t.strip().lower() for t in value.split(",") if t.strip()]
    return [CODE_TO_ENTITY.get(t, t) for t in raw_types]


def resolve_entity_model(entity_type: str) -> type[Base] | None:
    """Resolve SQLAlchemy model class from registry or Base mappers."""
    norm = entity_type.strip().lower()
    if norm in _ENTITY_REGISTRY:
        return _ENTITY_REGISTRY[norm]

    # Map plural module codes to singular entity if present
    singular = CODE_TO_ENTITY.get(norm, norm)
    if singular in _ENTITY_REGISTRY:
        return _ENTITY_REGISTRY[singular]

    # Inspect all registered SQLAlchemy mappers
    for mapper in Base.registry.mappers:
        cls: type[Base] = mapper.class_
        name = cls.__name__.lower()
        tbl = getattr(cls, "__tablename__", "").lower()

        # Check by class name, table name, or stripped prefixes/suffix
        normalized_tbl = (
            tbl.removeprefix("sys_").removeprefix("auth_").removesuffix("s")
        )
        if norm in (name, tbl, normalized_tbl) or singular in (
            name,
            tbl,
            normalized_tbl,
        ):
            _ENTITY_REGISTRY[norm] = cls
            _ENTITY_REGISTRY[singular] = cls
            return cls

    return None
