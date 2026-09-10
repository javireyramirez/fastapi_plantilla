"""Centralized helper for resolving actor identities across CRUD operations."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import UserReference
from fastapi_plantilla.modules.auth.models import User

__all__ = ["enrich_actors"]

ACTOR_ATTRS = (
    "created_by",
    "updated_by",
    "deleted_by",
    "restored_by",
    "actor_id",
)


def _extract_uuid(val: Any) -> uuid.UUID | None:
    """Safely convert a string or UUID value to a UUID instance."""
    if isinstance(val, uuid.UUID):
        return val
    if isinstance(val, str) and val.strip():
        try:
            return uuid.UUID(val.strip())
        except (ValueError, TypeError, AttributeError):
            return None
    return None


def _collect_actor_uuids(items: Sequence[Any]) -> dict[str, uuid.UUID]:
    """Collect unique UUIDs for actor attributes present on items."""
    raw_to_uuid: dict[str, uuid.UUID] = {}
    for item in items:
        for attr in ACTOR_ATTRS:
            raw_val = getattr(item, attr, None)
            if raw_val is not None:
                parsed = _extract_uuid(raw_val)
                if parsed is not None:
                    raw_to_uuid[str(raw_val).strip()] = parsed
    return raw_to_uuid


async def _fetch_users_map(
    session: AsyncSession, raw_to_uuid: dict[str, uuid.UUID]
) -> dict[str, UserReference]:
    """Fetch user references by UUIDs in a single batch query."""
    if not raw_to_uuid:
        return {}
    unique_uuids = list(set(raw_to_uuid.values()))
    stmt = select(User.id, User.name, User.email, User.image).where(
        User.id.in_(unique_uuids)
    )
    result = await session.execute(stmt)
    uuid_to_ref = {
        row.id: UserReference(
            id=row.id,
            name=row.name,
            email=row.email,
            image=row.image,
        )
        for row in result.all()
    }
    return {
        raw_str: uuid_to_ref[uid]
        for raw_str, uid in raw_to_uuid.items()
        if uid in uuid_to_ref
    }


def _attach_item_actors(item: Any, users_map: dict[str, UserReference]) -> None:
    """Attach actor objects and names to a single item in-place."""
    for attr, ref_attr, name_attr in (
        ("created_by", "creator", "created_by_name"),
        ("updated_by", "updater", "updated_by_name"),
        ("restored_by", "restorer", "restored_by_name"),
    ):
        if hasattr(item, attr):
            raw = getattr(item, attr, None)
            key = str(raw).strip() if raw is not None else ""
            user_ref = users_map.get(key)
            name = user_ref.name if user_ref else (str(raw) if raw else None)
            ref = user_ref or (UserReference(name=name) if name else None)
            setattr(item, ref_attr, ref)
            setattr(item, name_attr, name)

    if hasattr(item, "deleted_by"):
        raw = getattr(item, "deleted_by", None)
        key = str(raw).strip() if raw is not None else ""
        user_ref = users_map.get(key)
        name = user_ref.name if user_ref else (str(raw) if raw else None)
        email = user_ref.email if user_ref else None
        ref = user_ref or (
            UserReference(name=name, email=email) if name or email else None
        )
        item.deletor = ref  # type: ignore[attr-defined]
        item.deleted_by_name = name  # type: ignore[attr-defined]
        item.deleted_by_email = email  # type: ignore[attr-defined]

    if hasattr(item, "actor_id"):
        raw = getattr(item, "actor_id", None)
        key = str(raw).strip() if raw is not None else ""
        user_ref = users_map.get(key)
        name = user_ref.name if user_ref else getattr(item, "actor_name", None)
        email = user_ref.email if user_ref else getattr(item, "actor_email", None)
        if user_ref:
            if not getattr(item, "actor_name", None):
                item.actor_name = user_ref.name  # type: ignore[attr-defined]
            if not getattr(item, "actor_email", None):
                item.actor_email = user_ref.email  # type: ignore[attr-defined]
            item.user = user_ref  # type: ignore[attr-defined]
        elif name or email:
            item.user = UserReference(name=name, email=email)  # type: ignore[attr-defined]
        else:
            item.user = None  # type: ignore[attr-defined]


async def enrich_actors(
    session: AsyncSession,
    items: Sequence[Any],
) -> None:
    """Enrich models or schemas in-place with user details."""
    if not items:
        return

    raw_to_uuid = _collect_actor_uuids(items)
    users_map = await _fetch_users_map(session, raw_to_uuid)
    for item in items:
        _attach_item_actors(item, users_map)
