"""Centralized helper for resolving actor identities across CRUD operations."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import UserReference

__all__ = ["enrich_actors", "register_actor_model", "to_uuid"]

_user_model_cache: type[Any] | None = None


def register_actor_model(model: type[Any]) -> None:
    """Register the user model class used for resolving actor references."""
    global _user_model_cache  # noqa: PLW0603
    _user_model_cache = model


def _get_actor_model() -> type[Any] | None:
    global _user_model_cache  # noqa: PLW0603
    if _user_model_cache is None:
        try:
            from fastapi_plantilla.modules.auth.models import User  # noqa: PLC0415

            _user_model_cache = User
        except ImportError:
            return None
    return _user_model_cache


ACTOR_ATTRS = (
    "created_by",
    "updated_by",
    "deleted_by",
    "actor_id",
)


def to_uuid(val: Any) -> uuid.UUID | None:
    """Safely convert a string, int, or UUID value to a UUID instance."""
    if val is None or isinstance(val, uuid.UUID):
        return val
    if isinstance(val, str) and not val.strip():
        return None
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _collect_actor_uuids(items: Sequence[Any]) -> dict[str, uuid.UUID]:
    """Collect unique UUIDs for actor attributes present on items."""
    raw_to_uuid: dict[str, uuid.UUID] = {}
    for item in items:
        item_status = getattr(item, "status", None)
        status_val = getattr(item_status, "value", item_status)
        is_active = status_val == "ACTIVE"

        for attr in ACTOR_ATTRS:
            if attr == "deleted_by" and is_active:
                continue
            raw_val = getattr(item, attr, None)
            if raw_val is not None:
                parsed = to_uuid(raw_val)
                if parsed is not None:
                    raw_to_uuid[str(raw_val).strip()] = parsed
    return raw_to_uuid


async def _fetch_users_map(
    session: AsyncSession, raw_to_uuid: dict[str, uuid.UUID]
) -> dict[str, UserReference]:
    """Fetch user references by UUIDs in a single batch query."""
    if not raw_to_uuid:
        return {}
    user_model = _get_actor_model()
    if user_model is None:
        return {}
    unique_uuids = list(set(raw_to_uuid.values()))
    stmt = select(user_model.id, user_model.name, user_model.email).where(
        user_model.id.in_(unique_uuids)
    )
    result = await session.execute(stmt)
    uuid_to_ref = {
        row.id: UserReference(
            id=row.id,
            name=row.name,
            email=row.email,
        )
        for row in result.all()
    }
    return {
        raw_str: uuid_to_ref[uid]
        for raw_str, uid in raw_to_uuid.items()
        if uid in uuid_to_ref
    }


def _resolve_actor(
    raw: Any,
    users_map: dict[str, UserReference],
    fallback_name: str | None = None,
    fallback_email: str | None = None,
) -> UserReference | None:
    """Resolve raw actor reference against known users or fallbacks."""
    key = str(raw).strip() if raw is not None else ""
    user_ref = users_map.get(key)
    if user_ref:
        return user_ref
    name = fallback_name or (str(raw) if raw else None)
    email = fallback_email
    if name or email or raw:
        return UserReference(id=to_uuid(raw), name=name, email=email)
    return None


def _attach_item_actors(item: Any, users_map: dict[str, UserReference]) -> None:
    """Attach actor objects to a single item in-place."""
    for raw_attr, target_attr in (
        ("created_by", "creator"),
        ("updated_by", "updater"),
    ):
        if hasattr(item, raw_attr):
            setattr(
                item,
                target_attr,
                _resolve_actor(getattr(item, raw_attr, None), users_map),
            )

    item_status = getattr(item, "status", None)
    status_val = getattr(item_status, "value", item_status)
    if status_val != "ACTIVE" and hasattr(item, "deleted_by"):
        item.deletor = _resolve_actor(getattr(item, "deleted_by", None), users_map)

    if hasattr(item, "actor_id"):
        raw_actor_id = getattr(item, "actor_id", None)
        fallback_name = getattr(item, "actor_name", None)
        fallback_email = getattr(item, "actor_email", None)
        item.user = _resolve_actor(  # type: ignore[attr-defined]
            raw_actor_id,
            users_map,
            fallback_name=fallback_name,
            fallback_email=fallback_email,
        )


async def enrich_actors(
    session: AsyncSession,
    items: Sequence[Any],
    known_users: Sequence[UserReference] | dict[str, UserReference] | None = None,
) -> None:
    """Enrich models or schemas in-place with user details."""
    if not items:
        return

    users_map: dict[str, UserReference] = {}
    if known_users:
        if isinstance(known_users, dict):
            users_map.update(known_users)
        else:
            for u in known_users:
                if u and u.id is not None:
                    users_map[str(u.id).strip()] = u

    raw_to_uuid = _collect_actor_uuids(items)
    needed_uuids = {
        raw_str: uid
        for raw_str, uid in raw_to_uuid.items()
        if raw_str not in users_map and str(uid) not in users_map
    }

    if needed_uuids:
        fetched_map = await _fetch_users_map(session, needed_uuids)
        users_map.update(fetched_map)

    for item in items:
        _attach_item_actors(item, users_map)
