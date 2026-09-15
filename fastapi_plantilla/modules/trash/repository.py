import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, inspect, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import (
    ScopeContext,
    ScopeType,
    SortOrder,
)
from fastapi_plantilla.core.crud.service_base import adjust_end_of_day
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.common.schema import EntityType
from fastapi_plantilla.modules.rbac.catalog import CORE_SYSTEM_MODULES
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    TrashFilterParams,
)

__all__ = [
    "TrashRepository",
    "register_trash_model",
    "resolve_model",
]


_ENTITY_REGISTRY: dict[str, type[Base]] = {}


def register_trash_model(entity_type: str, model: type[Base]) -> None:
    """Register custom entity model for trash persistence operations."""
    _ENTITY_REGISTRY[entity_type.strip().lower()] = model


def resolve_model(entity_type: str) -> type[Base] | None:
    """Resolve SQLAlchemy model class from registry or Base mappers."""
    norm = entity_type.strip().lower()
    if norm in _ENTITY_REGISTRY:
        return _ENTITY_REGISTRY[norm]

    for mapper in Base.registry.mappers:
        cls = mapper.class_
        name = cls.__name__.lower()
        tbl = getattr(cls, "__tablename__", "").lower()
        if norm in (name, tbl):
            _ENTITY_REGISTRY[norm] = cls
            return cls
    return None


def _build_category_filter(category: str | None) -> Any | None:
    """Return filter clause for category grouping if applicable."""
    if category in ("storage", "files"):
        return TrashItem.entity_type == EntityType.STORAGE.value
    if category in ("entities", "business", "security"):
        return TrashItem.entity_type != EntityType.STORAGE.value
    if category:
        matching = [
            mod["code"].lower()
            for mod in CORE_SYSTEM_MODULES
            if mod.get("category") == category
        ]
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
        clauses.append(TrashItem.deleted_at <= adjust_end_of_day(params.deleted_at_to))
    if params.expires_at_from:
        clauses.append(TrashItem.expires_at >= params.expires_at_from)
    if params.expires_at_to:
        clauses.append(TrashItem.expires_at <= adjust_end_of_day(params.expires_at_to))
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
        raw_types = [
            t.strip().lower() for t in params.entity_type.split(",") if t.strip()
        ]
        if len(raw_types) == 1:
            where.append(TrashItem.entity_type == raw_types[0])
        elif raw_types:
            where.append(TrashItem.entity_type.in_(raw_types))

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
        return await self.find_first(
            TrashItem.entity_type == entity_type.strip().lower(),
            TrashItem.entity_id == entity_id,
        )

    async def delete_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> int:
        """Remove trash record associated with a specific entity."""
        stmt = delete(TrashItem).where(
            TrashItem.entity_type == entity_type.strip().lower(),
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
        from fastapi_plantilla.modules.common.principal import (  # noqa: PLC0415
            resolve_principal_entity_name,
        )

        return await resolve_principal_entity_name(self.session, entity_type, entity_id)

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
