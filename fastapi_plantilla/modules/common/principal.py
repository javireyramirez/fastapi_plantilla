"""Centralized helper for resolving parent/principal entity metadata across modules."""

import uuid
from collections.abc import Sequence
from typing import Any

from loguru import logger
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.modules.common.schema import (
    PrincipalEntityModule,
)

__all__ = [
    "enrich_principal_entities",
    "resolve_principal_entity_name",
]

from fastapi_plantilla.modules.common.resolvers import resolve_entity_model


def _get_known_model(entity_type: str) -> type[Base] | None:
    """Resolve SQLAlchemy model class for entity type with centralized SSOT."""
    return resolve_entity_model(entity_type)


async def resolve_principal_entity_name(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
) -> str | None:
    """Query database to get human-readable name for any entity generically."""
    model = _get_known_model(entity_type)
    if model is None:
        return None

    for attr in ("name", "title", "username", "code", "email"):
        col = getattr(model, attr, None)
        if col is not None:
            try:
                pk_col = inspect(model).primary_key[0]
                stmt = select(col).where(pk_col == entity_id)
                res = await session.execute(stmt)
                val = res.scalar_one_or_none()
                if val is not None:
                    return str(val)
            except Exception as err:
                logger.debug(
                    f"Could not resolve entity name for {entity_type}: "
                    f"{entity_id}: {err}"
                )
                return None
    return None


async def enrich_principal_entities(
    session: AsyncSession,
    items: Sequence[Any],
) -> None:
    """Enrich items with resolved module_principal_entity including entity_name."""
    if not items:
        return

    # Collect unique (entity_type, entity_id) targets to resolve
    targets: set[tuple[str, uuid.UUID]] = set()
    for item in items:
        existing = (
            item.get("module_principal_entity")
            if isinstance(item, dict)
            else getattr(item, "module_principal_entity", None)
        )
        if isinstance(existing, PrincipalEntityModule) and existing.entity_name:
            continue

        etype = (
            item.get("entity_type")
            if isinstance(item, dict)
            else (
                getattr(item, "entity_type", None)
                or getattr(item, "target_entity_type", None)
            )
        )
        eid = (
            item.get("entity_id")
            if isinstance(item, dict)
            else (
                getattr(item, "entity_id", None)
                or getattr(item, "target_entity_id", None)
            )
        )
        if etype and eid:
            targets.add((str(etype).strip().lower(), eid))

    # Resolve names in batch per unique entity
    resolved_names: dict[tuple[str, uuid.UUID], str | None] = {}
    for etype, eid in targets:
        resolved_names[(etype, eid)] = await resolve_principal_entity_name(
            session, etype, eid
        )

    # Attach module_principal_entity to each item
    for item in items:
        etype = (
            item.get("entity_type")
            if isinstance(item, dict)
            else (
                getattr(item, "entity_type", None)
                or getattr(item, "target_entity_type", None)
            )
        )
        eid = (
            item.get("entity_id")
            if isinstance(item, dict)
            else (
                getattr(item, "entity_id", None)
                or getattr(item, "target_entity_id", None)
            )
        )
        if not etype or not eid:
            continue

        norm_type = str(etype).strip().lower()
        from fastapi_plantilla.modules.trash.service import (  # noqa: PLC0415
            resolve_module,
        )

        target_mod = resolve_module(norm_type)
        code = target_mod.code if target_mod else norm_type
        name = target_mod.name if target_mod else norm_type.capitalize()
        entity_name = resolved_names.get((norm_type, eid))

        principal = PrincipalEntityModule(
            code=code,
            name=name,
            entity_name=entity_name,
            entity_id=eid,
        )
        if isinstance(item, dict):
            item["module_principal_entity"] = principal
        else:
            item.module_principal_entity = principal
