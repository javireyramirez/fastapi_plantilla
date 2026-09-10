import uuid
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import delete, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import ScopeContext, ScopeType, SortOrder
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.rbac.catalog import CORE_SYSTEM_MODULES
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    TrashFilterParams,
)

__all__ = [
    "TrashRepository",
    "register_trash_model",
    "resolve_entity_type_candidates",
    "resolve_model",
]

_ENTITY_REGISTRY: dict[str, type[Base]] = {}


def register_trash_model(entity_type: str, model: type[Base]) -> None:
    """Register custom entity model for trash persistence operations."""
    _ENTITY_REGISTRY[entity_type.strip().lower()] = model


_TYPE_ALIASES: dict[str, list[str]] = {
    "company": ["company", "companies"],
    "companies": ["company", "companies"],
    "document": ["document", "documents"],
    "documents": ["document", "documents"],
    "user": ["user", "users"],
    "users": ["user", "users"],
    "team": ["team", "teams"],
    "teams": ["team", "teams"],
    "role": ["role", "roles"],
    "roles": ["role", "roles"],
}


def resolve_entity_type_candidates(raw_type: str) -> list[str]:
    """Normalize entity type into known singular and plural forms."""
    norm = raw_type.strip().lower()
    return _TYPE_ALIASES.get(norm, [norm])


def resolve_model(entity_type: str) -> type[Base] | None:
    """Resolve SQLAlchemy model class from registry or Base mappers."""
    normalized = entity_type.strip().lower()
    if normalized in _ENTITY_REGISTRY:
        return _ENTITY_REGISTRY[normalized]

    candidates = set(resolve_entity_type_candidates(normalized))
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        name = cls.__name__.lower()
        tbl = getattr(cls, "__tablename__", "").lower()
        if name in candidates or tbl in candidates:
            _ENTITY_REGISTRY[normalized] = cls
            return cls
    return None


def _build_category_filter(category: str | None) -> Any | None:
    """Return filter clause for category grouping if applicable."""
    if category == "documents":
        return TrashItem.entity_type.in_(["document", "documents"])
    if category == "entities":
        return ~TrashItem.entity_type.in_(["document", "documents"])
    if category:
        matching: list[str] = []
        for mod in CORE_SYSTEM_MODULES:
            if mod.get("category") == category:
                matching.extend(resolve_entity_type_candidates(mod["code"]))
        if matching:
            return TrashItem.entity_type.in_(matching)
    return None


def _build_date_filters(params: TrashFilterParams) -> list[Any]:
    """Build date and expiration filter clauses for trash bin."""
    clauses: list[Any] = []
    now = datetime.now(UTC)
    if params.is_expired is True:
        clauses.append(TrashItem.expires_at <= now)
    elif params.is_expired is False:
        clauses.append(TrashItem.expires_at > now)

    if params.deleted_at_from:
        clauses.append(TrashItem.deleted_at >= params.deleted_at_from)
    if params.deleted_at_to:
        clauses.append(TrashItem.deleted_at <= params.deleted_at_to)
    if params.expires_at_from:
        clauses.append(TrashItem.expires_at >= params.expires_at_from)
    if params.expires_at_to:
        clauses.append(TrashItem.expires_at <= params.expires_at_to)
    return clauses


def _build_trash_filters(
    params: TrashFilterParams,
    scope: ScopeContext | None = None,
) -> list[Any]:
    """Build SQLAlchemy query filter clauses for trash bin listings."""
    where: list[Any] = []

    if scope and not scope.is_super_admin and str(scope.scope).upper() == ScopeType.OWN:
        where.append(TrashItem.owner_id == scope.user_id)

    cat_clause = _build_category_filter(params.category)
    if cat_clause is not None:
        where.append(cat_clause)

    if params.entity_type:
        candidates = resolve_entity_type_candidates(params.entity_type)
        where.append(TrashItem.entity_type.in_(candidates))

    where.extend(_build_date_filters(params))

    search_query = params.q or params.search
    if search_query:
        pat = f"%{search_query}%"
        where.append(
            or_(
                TrashItem.name.ilike(pat),
                TrashItem.target_entity_name.ilike(pat),
            )
        )

    return where


class TrashRepository(BaseRepository[TrashItem]):
    """Database repository for centralized trash bin operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(TrashItem, session)

    async def get_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> TrashItem | None:
        """Fetch trash record for a specific polymorphic entity."""
        types = resolve_entity_type_candidates(entity_type)
        return await self.find_first(
            TrashItem.entity_type.in_(types),
            TrashItem.entity_id == entity_id,
        )

    async def delete_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> int:
        """Remove trash record associated with a specific entity."""
        types = resolve_entity_type_candidates(entity_type)
        stmt = delete(TrashItem).where(
            TrashItem.entity_type.in_(types),
            TrashItem.entity_id == entity_id,
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def find_trash_items(
        self,
        params: TrashFilterParams,
        scope: ScopeContext | None = None,
    ) -> tuple[list[TrashItem], int]:
        """Fetch filtered and paginated trash items with count."""
        where = _build_trash_filters(params, scope)
        sort_col = getattr(TrashItem, params.sort_by, TrashItem.deleted_at)
        order_expr = (
            sort_col.asc() if params.sort_order == SortOrder.ASC else sort_col.desc()
        )
        skip = (params.page - 1) * params.limit
        return await self.find_many_with_count(
            *where,
            skip=skip,
            limit=params.limit,
            order_by=order_expr,
        )

    async def resolve_entity_name(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> str | None:
        """Query database to get human-readable name for any entity generically."""
        model = resolve_model(entity_type)
        if model is None:
            return None

        for attr in ("name", "title", "username", "code", "email"):
            col = getattr(model, attr, None)
            if col is not None:
                try:
                    pk_col = inspect(model).primary_key[0]
                    stmt = select(col).where(pk_col == entity_id)
                    res = await self.session.execute(stmt)
                    val = res.scalar_one_or_none()
                    if val:
                        return str(val)
                except Exception as err:
                    logger.debug(
                        f"Could not resolve name for {entity_type}:{entity_id}: {err}"
                    )
                    return None
        return None

    async def restore_target_model(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Update source domain entity status back to ACTIVE."""
        model = resolve_model(entity_type)
        if model is not None and hasattr(model, "status"):
            now = datetime.now(UTC)
            actor = str(user_id) if user_id else None
            pk_col = inspect(model).primary_key[0]
            stmt = (
                update(model)
                .where(pk_col == entity_id)
                .values(
                    status=RecordStatus.ACTIVE,
                    restored_at=now,
                    restored_by=actor,
                )
            )
            await self.session.execute(stmt)

    async def purge_target_model(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> None:
        """Permanently delete source domain entity row from database."""
        model = resolve_model(entity_type)
        if model is not None:
            pk_col = inspect(model).primary_key[0]
            stmt = delete(model).where(pk_col == entity_id)
            await self.session.execute(stmt)

    async def save_trash_item(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        name: str,
        owner_id: uuid.UUID | None,
        deleted_by: str | None,
        details: str | None,
        expires_at: datetime,
        target_entity_type: str | None = None,
        target_entity_id: uuid.UUID | None = None,
        target_entity_name: str | None = None,
    ) -> TrashItem:
        """Create or update a record in sys_trash_bin."""
        normalized = entity_type.strip().lower()
        existing = await self.get_by_entity(normalized, entity_id)
        now = datetime.now(UTC)
        if existing is not None:
            existing.name = name
            existing.deleted_by = deleted_by
            existing.deleted_at = now
            existing.expires_at = expires_at
            existing.details = details
            if target_entity_type is not None:
                existing.target_entity_type = target_entity_type
            if target_entity_id is not None:
                existing.target_entity_id = target_entity_id
            if target_entity_name is not None:
                existing.target_entity_name = target_entity_name
            await self.session.flush()
            return existing

        return await self.create(
            {
                "entity_type": normalized,
                "entity_id": entity_id,
                "name": name,
                "owner_id": owner_id,
                "deleted_by": deleted_by,
                "deleted_at": now,
                "expires_at": expires_at,
                "details": details,
                "target_entity_type": target_entity_type,
                "target_entity_id": target_entity_id,
                "target_entity_name": target_entity_name,
            }
        )

    async def find_expired(
        self,
        cutoff: datetime,
        limit: int = DEFAULT_TRASH_PURGE_LIMIT,
    ) -> list[TrashItem]:
        """Fetch trash items whose retention period has expired."""
        return await self.find_many(
            TrashItem.expires_at <= cutoff,
            limit=limit,
            order_by=TrashItem.expires_at.asc(),
        )

    async def count_expired(self, cutoff: datetime) -> int:
        """Count the number of expired trash items."""
        return await self.count(TrashItem.expires_at <= cutoff)
