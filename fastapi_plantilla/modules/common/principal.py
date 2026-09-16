"""Centralized helper for resolving parent/principal entity metadata across modules."""

import uuid
from collections.abc import Sequence
from typing import Any

from loguru import logger
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.actors import to_uuid
from fastapi_plantilla.modules.common.resolvers import (
    resolve_entity_model,
    resolve_module_metadata,
)
from fastapi_plantilla.modules.common.schema import (
    PrincipalEntityModule,
)

__all__ = [
    "enrich_principal_entities",
    "resolve_principal_entity_name",
]

# Preferred column names to represent a human-readable entity name
_NAME_ATTRS = ("name", "title", "username", "code", "email")


def _extract_item_entity(item: Any) -> tuple[str, uuid.UUID] | None:
    """Extract normalized entity_type and valid entity_id from dict or object."""
    if isinstance(item, dict):
        raw_type = item.get("entity_type") or item.get("target_entity_type")
        raw_id = item.get("entity_id") or item.get("target_entity_id")
    else:
        raw_type = getattr(item, "entity_type", None) or getattr(
            item, "target_entity_type", None
        )
        raw_id = getattr(item, "entity_id", None) or getattr(
            item, "target_entity_id", None
        )

    parsed_id = to_uuid(raw_id)
    if raw_type and parsed_id:
        return str(raw_type).strip().lower(), parsed_id
    return None


async def resolve_principal_entity_name(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID | str,
) -> str | None:
    """Query database to get human-readable name for any entity generically."""
    parsed_id = to_uuid(entity_id)
    if parsed_id is None:
        return None

    model = resolve_entity_model(entity_type)
    if model is None:
        return None

    try:
        pk_col = inspect(model).primary_key[0]
    except Exception:
        return None

    for attr in _NAME_ATTRS:
        col = getattr(model, attr, None)
        if col is not None:
            try:
                stmt = select(col).where(pk_col == parsed_id)
                res = await session.execute(stmt)
                val = res.scalar_one_or_none()
                if val is not None:
                    return str(val)
            except Exception as err:
                logger.debug(
                    f"Could not resolve entity name for {entity_type}: "
                    f"{parsed_id}: {err}"
                )
                return None
    return None


async def _batch_resolve_type_names(
    session: AsyncSession,
    etype: str,
    ids: set[uuid.UUID],
) -> dict[uuid.UUID, str | None]:
    """Batch query human-readable names for one entity type using IN clause."""
    results: dict[uuid.UUID, str | None] = dict.fromkeys(ids)
    model = resolve_entity_model(etype)
    if model is None:
        return results

    try:
        pk_col = inspect(model).primary_key[0]
    except Exception:
        return results

    name_col = next(
        (
            getattr(model, a, None)
            for a in _NAME_ATTRS
            if getattr(model, a, None) is not None
        ),
        None,
    )
    if name_col is None:
        return results

    try:
        stmt = select(pk_col, name_col).where(pk_col.in_(ids))
        res = await session.execute(stmt)
        for row in res.all():
            results[row[0]] = str(row[1]) if row[1] is not None else None
    except Exception as err:
        logger.debug(f"Batch name lookup failed for {etype}: {err}")
        for eid in ids:
            results[eid] = await resolve_principal_entity_name(session, etype, eid)

    return results


def _is_already_enriched(item: Any) -> bool:
    """Check if item already has resolved module_principal_entity with name."""
    existing = (
        item.get("module_principal_entity")
        if isinstance(item, dict)
        else getattr(item, "module_principal_entity", None)
    )
    return bool(isinstance(existing, PrincipalEntityModule) and existing.entity_name)


def _attach_principal(item: Any, principal: PrincipalEntityModule) -> None:
    """Attach PrincipalEntityModule metadata to dictionary or model object."""
    if isinstance(item, dict):
        item["module_principal_entity"] = principal
    else:
        item.module_principal_entity = principal


def _collect_targets(items: Sequence[Any]) -> set[tuple[str, uuid.UUID]]:
    """Collect unique unenriched (entity_type, entity_id) pairs from items."""
    targets: set[tuple[str, uuid.UUID]] = set()
    for item in items:
        if _is_already_enriched(item):
            continue
        target = _extract_item_entity(item)
        if target:
            targets.add(target)
    return targets


async def enrich_principal_entities(
    session: AsyncSession,
    items: Sequence[Any],
) -> None:
    """
    Enrich items with resolved module_principal_entity including entity_name.

    Performs batch SQL queries per entity type using IN clauses to eliminate
    N+1 database roundtrips.
    """
    if not items:
        return

    targets = _collect_targets(items)

    # Group entity IDs by entity type
    by_type: dict[str, set[uuid.UUID]] = {}
    for etype, eid in targets:
        by_type.setdefault(etype, set()).add(eid)

    # Resolve names per entity type in batch
    resolved_names: dict[tuple[str, uuid.UUID], str | None] = {}
    for etype, ids in by_type.items():
        type_names = await _batch_resolve_type_names(session, etype, ids)
        for eid, name in type_names.items():
            resolved_names[(etype, eid)] = name

    # Attach module_principal_entity to each item
    for item in items:
        if _is_already_enriched(item):
            continue
        target = _extract_item_entity(item)
        if not target:
            continue

        norm_type, parsed_id = target
        code, name = resolve_module_metadata(norm_type)
        principal = PrincipalEntityModule(
            code=code,
            name=name,
            entity_name=resolved_names.get((norm_type, parsed_id)),
            entity_id=parsed_id,
        )
        _attach_principal(item, principal)
