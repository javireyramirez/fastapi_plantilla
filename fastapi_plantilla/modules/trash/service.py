import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import delete, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.actors import enrich_actors
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
    ScopeType,
    SortOrder,
)
from fastapi_plantilla.core.crud.service_audit import _AUDIT_SYNC_HOOKS
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.rbac.catalog import CORE_SYSTEM_MODULES
from fastapi_plantilla.modules.rbac.models import Role, SystemModule
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    BulkTrashActionRequest,
    TrashFilterParams,
    TrashItemResponse,
    TrashModuleResponse,
)

__all__ = [
    "TrashService",
    "register_trash_entity",
]

# Type alias for purge callbacks
PurgeCallback = Callable[[AsyncSession, uuid.UUID], Coroutine[Any, Any, None]]

_ENTITY_REGISTRY: dict[str, type[Base]] = {}
_PURGE_HOOKS: dict[str, PurgeCallback] = {}


def register_trash_entity(
    entity_type: str,
    model: type[Base],
    purge_hook: PurgeCallback | None = None,
) -> None:
    """Register domain model and cleanup callback for trash operations."""
    normalized_type = entity_type.strip().lower()
    _ENTITY_REGISTRY[normalized_type] = model
    if purge_hook is not None:
        _PURGE_HOOKS[normalized_type] = purge_hook


TRASH_TYPE_MAPPINGS: dict[tuple[str, ...], list[str]] = {
    ("company", "companies", "companie"): ["company", "companies", "companie"],
    ("user", "users", "auth_user", "auth_users"): [
        "user",
        "users",
        "auth_user",
        "auth_users",
    ],
    ("team", "teams"): ["team", "teams"],
    ("role", "roles"): ["role", "roles"],
    ("document", "documents"): ["document", "documents"],
}


def _resolve_trash_entity_types(raw_entity_type: str) -> list[str]:
    """Resolve comma-separated entity types into candidate table values."""
    raw_types = [t.strip().lower() for t in raw_entity_type.split(",") if t.strip()]
    candidates: set[str] = set()
    for raw in raw_types:
        candidates.add(raw)
        for keys, values in TRASH_TYPE_MAPPINGS.items():
            if raw in keys:
                candidates.update(values)
    return list(candidates)


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
    is_own = bool(
        scope and not scope.is_super_admin and str(scope.scope).upper() == ScopeType.OWN
    )
    if is_own and scope:
        where.append(TrashItem.owner_id == scope.user_id)

    if params.category == "documents":
        where.append(TrashItem.entity_type.in_(["document", "documents"]))
    elif params.category == "entities":
        where.append(~TrashItem.entity_type.in_(["document", "documents"]))

    if params.entity_type:
        candidates = _resolve_trash_entity_types(params.entity_type)
        if len(candidates) == 1:
            where.append(TrashItem.entity_type == candidates[0])
        elif len(candidates) > 1:
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


# ---------------------------------------------------------------------------
# Module / entity enrichment helpers
# ---------------------------------------------------------------------------

_ENTITY_NAME_MODELS: dict[str, tuple[Any, bool]] = {
    "user": (User, True),
    "users": (User, True),
    "company": (Company, False),
    "companies": (Company, False),
    "team": (Team, False),
    "teams": (Team, False),
    "role": (Role, False),
    "roles": (Role, False),
}

_MODULE_CACHE: dict[str, TrashModuleResponse | None] = {}


def _get_module_for_type_static(entity_type: str) -> TrashModuleResponse | None:
    """Return module metadata from CORE_SYSTEM_MODULES for a given entity type."""
    if entity_type in _MODULE_CACHE:
        return _MODULE_CACHE[entity_type]

    normalized = entity_type.strip().lower().rstrip("s")
    for mod in CORE_SYSTEM_MODULES:
        code = mod["code"]
        candidates = {entity_type, normalized, normalized + "s"}
        if code in candidates or code.rstrip("s") == normalized:
            result = TrashModuleResponse(
                code=code,
                name=mod["name"],
                description=mod.get("description"),
                icon=mod.get("icon"),
                category=mod.get("category"),
            )
            _MODULE_CACHE[entity_type] = result
            return result

    _MODULE_CACHE[entity_type] = None
    return None


async def _get_module_from_db(
    session: AsyncSession, entity_type: str
) -> TrashModuleResponse | None:
    """Query sys_modules table for module matching entity_type as fallback."""
    normalized = entity_type.strip().lower().rstrip("s")
    try:
        stmt = select(SystemModule).where(
            or_(
                SystemModule.code == entity_type,
                SystemModule.code == normalized,
            )
        )
        res = await session.execute(stmt)
        mod = res.scalar_one_or_none()
        if mod is not None:
            return TrashModuleResponse(
                id=mod.id,
                code=mod.code,
                slug=getattr(mod, "slug", None),
                name=mod.name,
                description=getattr(mod, "description", None),
                icon=getattr(mod, "icon", None),
                category=getattr(mod, "category", None),
            )
    except Exception as err:
        logger.debug(f"Module DB lookup failed for {entity_type!r}: {err}")
    return None


async def _resolve_module(
    session: AsyncSession, entity_type: str
) -> TrashModuleResponse | None:
    """Resolve module from static catalog first, then DB."""
    mod = _get_module_for_type_static(entity_type)
    if mod is not None:
        return mod
    return await _get_module_from_db(session, entity_type)


async def _resolve_entity_name(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> str | None:
    """Query DB to get a human-readable name for an entity."""
    mapping = _ENTITY_NAME_MODELS.get(entity_type.strip().lower())
    if mapping is None:
        return None
    model, is_user = mapping
    try:
        cols = (
            [model.id, model.name, model.email] if is_user else [model.id, model.name]
        )
        stmt = select(*cols).where(model.id == entity_id)
        res = await session.execute(stmt)
        row = res.one_or_none()
        if row is None:
            return None
        return (row.name or row.email) if is_user else row.name
    except Exception as err:
        logger.debug(f"Entity name lookup failed for {entity_type}:{entity_id}: {err}")
        return None


async def _fetch_doc_targets(session: AsyncSession, items: list[TrashItem]) -> None:
    """Fill target_entity_type/id for document items that lack them."""
    from fastapi_plantilla.modules.storage.models import Document  # noqa: PLC0415

    need_lookup = [
        item
        for item in items
        if item.target_entity_type is None or item.target_entity_id is None
    ]
    if not need_lookup:
        return
    doc_ids = [item.entity_id for item in need_lookup]
    try:
        stmt = select(Document.id, Document.entity_type, Document.entity_id).where(
            Document.id.in_(doc_ids)
        )
        rows = (await session.execute(stmt)).all()
        doc_map = {r.id: (r.entity_type, r.entity_id) for r in rows}
    except Exception as err:
        logger.warning(f"Document target lookup failed: {err}")
        doc_map = {}
    for item in need_lookup:
        info = doc_map.get(item.entity_id)
        if info:
            item.__dict__["target_entity_type"] = info[0]
            item.__dict__["target_entity_id"] = info[1]


async def _enrich_doc_items(session: AsyncSession, items: list[TrashItem]) -> None:
    """Enrich document-typed trash items with target entity name and module."""
    await _fetch_doc_targets(session, items)
    for item in items:
        tet = item.target_entity_type
        tei = item.target_entity_id
        if item.target_entity_name is None and tet and tei:
            name = await _resolve_entity_name(session, tet, tei)
            item.__dict__["target_entity_name"] = name
        mod_type = tet if tet else "documents"
        mod = await _resolve_module(session, mod_type)
        if mod is not None:
            item.module = mod  # type: ignore[attr-defined]


async def _enrich_non_doc_items(session: AsyncSession, items: list[TrashItem]) -> None:
    """Enrich non-document trash items with entity name and module."""
    for item in items:
        etype = item.entity_type
        item.__dict__["target_entity_type"] = etype
        item.__dict__["target_entity_id"] = item.entity_id
        has_name = item.__dict__.get("target_entity_name") or item.target_entity_name
        if not has_name:
            item.__dict__["target_entity_name"] = item.name
        mod = await _resolve_module(session, etype)
        if mod is not None:
            item.module = mod  # type: ignore[attr-defined]


async def _enrich_module_and_entity(
    session: AsyncSession, items: list[TrashItem]
) -> None:
    """Enrich trash items in-place with module info and target entity details.

    For document items: resolves the parent entity (company/user/team/role)
    from the Document row, then assigns module metadata for the parent type.
    For non-document items: uses the item's own entity_type for module lookup.
    """
    doc_items = [i for i in items if i.entity_type in ("document", "documents")]
    non_doc_items = [i for i in items if i.entity_type not in ("document", "documents")]
    if doc_items:
        await _enrich_doc_items(session, doc_items)
    if non_doc_items:
        await _enrich_non_doc_items(session, non_doc_items)


class TrashService:
    """Service orchestrating trash bin queries, restorations and physical purges."""

    def __init__(self, repository: TrashRepository) -> None:
        self.repository = repository
        self.session: AsyncSession = repository.session

    def _resolve_model(self, entity_type: str) -> type[Base] | None:
        """Find SQLAlchemy model class registered for the given entity type."""
        normalized = entity_type.strip().lower()
        if normalized in _ENTITY_REGISTRY:
            return _ENTITY_REGISTRY[normalized]

        candidates = (
            normalized,
            f"sys_{normalized}",
            f"{normalized}s",
            f"sys_{normalized}s",
        )
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            tablename = getattr(cls, "__tablename__", "")
            if tablename in candidates:
                return cls  # type: ignore[no-any-return]
        return None

    def _apply_scope(self, scope: ScopeContext | None, item: TrashItem) -> None:
        """Enforce owner-level RBAC access permissions on single item."""
        if not scope or scope.is_super_admin:
            return
        if str(scope.scope).upper() != ScopeType.OWN:
            return
        if item.owner_id is not None and item.owner_id != scope.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )

    async def _enrich_items(self, items: list[TrashItem]) -> None:
        """Enrich trash items in-place with deletor info, module, and target entity."""
        await enrich_actors(self.session, items)
        await _enrich_module_and_entity(self.session, items)

    async def list_trash(
        self,
        params: TrashFilterParams,
        scope: ScopeContext | None = None,
    ) -> PaginatedResponse[TrashItemResponse]:
        """Fetch paginated trash items matching criteria and RBAC scope."""
        where = _build_trash_filters(params, scope)

        sort_col = getattr(TrashItem, params.sort_by, TrashItem.deleted_at)
        order_expr = (
            sort_col.asc() if params.sort_order == SortOrder.ASC else sort_col.desc()
        )

        skip = (params.page - 1) * params.limit
        items, total = await self.repository.find_many_with_count(
            *where,
            skip=skip,
            limit=params.limit,
            order_by=order_expr,
        )

        await self._enrich_items(items)
        response_items = [TrashItemResponse.model_validate(item) for item in items]
        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=response_items, meta=meta)

    async def get_by_id(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> TrashItem:
        """Fetch single trash record verifying RBAC scope."""
        item = await self.repository.get_by_id(id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )
        self._apply_scope(scope, item)
        await self._enrich_items([item])
        return item

    async def record_trash(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        name: str,
        owner_id: uuid.UUID | None = None,
        deleted_by: str | None = None,
        details: str | None = None,
        data_backup: dict[str, Any] | None = None,
        retention_days: int | None = None,
        target_entity_type: str | None = None,
        target_entity_id: uuid.UUID | None = None,
        target_entity_name: str | None = None,
    ) -> TrashItem:
        """Record an entity in the centralized trash bin."""
        days = retention_days or settings.trash_retention_days
        expires_at = datetime.now(UTC) + timedelta(days=days)
        normalized_type = entity_type.strip().lower()

        existing = await self.repository.get_by_entity(normalized_type, entity_id)
        if existing is not None:
            existing.name = name
            existing.deleted_by = deleted_by
            existing.deleted_at = datetime.now(UTC)
            existing.expires_at = expires_at
            existing.data_backup = data_backup
            existing.details = details
            if target_entity_type is not None:
                existing.target_entity_type = target_entity_type
            if target_entity_id is not None:
                existing.target_entity_id = target_entity_id
            if target_entity_name is not None:
                existing.target_entity_name = target_entity_name
            await self.session.flush()
            return existing

        return await self.repository.create(
            {
                "entity_type": normalized_type,
                "entity_id": entity_id,
                "name": name,
                "owner_id": owner_id,
                "deleted_by": deleted_by,
                "deleted_at": datetime.now(UTC),
                "expires_at": expires_at,
                "data_backup": data_backup,
                "details": details,
                "target_entity_type": target_entity_type,
                "target_entity_id": target_entity_id,
                "target_entity_name": target_entity_name,
            }
        )

    async def _emit_audit(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        entity_name: str | None = None,
        actor_id: uuid.UUID | None = None,
        changes: dict[str, Any] | None = None,
        details: str | None = None,
    ) -> None:
        """Dispatch audit event to registered audit sync listeners."""
        if not _AUDIT_SYNC_HOOKS:
            return
        entry = AuditEntry(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            action=action,
            actor_id=actor_id,
            changes=changes,
            details=details,
        )
        for hook in _AUDIT_SYNC_HOOKS:
            try:
                await hook(self.session, entry)
            except Exception as err:
                logger.warning(f"Trash audit hook execution failed: {err}")

    async def restore_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> TrashItem:
        """Restore an item from trash back to active state in its source module."""
        item = await self.get_by_id(id, scope=scope)
        model = self._resolve_model(item.entity_type)

        if model is not None and hasattr(model, "status"):
            now = datetime.now(UTC)
            actor = str(user_id) if user_id else None
            pk_col = inspect(model).primary_key[0]
            stmt = (
                update(model)
                .where(pk_col == item.entity_id)
                .values(
                    status=RecordStatus.ACTIVE,
                    restored_at=now,
                    restored_by=actor,
                )
            )
            await self.session.execute(stmt)

        await self.repository.delete(item.id)

        actor_uuid = None
        if user_id:
            try:
                actor_uuid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                actor_uuid = None

        await self._emit_audit(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            action="RESTORE",
            entity_name=item.name,
            actor_id=actor_uuid,
            changes={
                "status": {
                    "old": RecordStatus.TRASHED.value,
                    "new": RecordStatus.ACTIVE.value,
                }
            },
            details=f"Restored {item.name or item.entity_type} from trash",
        )
        return item

    async def purge_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> TrashItem:
        """Permanently delete an item from the source table, storage, and trash bin."""
        item = await self.get_by_id(id, scope=scope)
        normalized_type = item.entity_type.strip().lower()

        # Run custom purge hook if registered (e.g. to clean S3 files)
        if normalized_type in _PURGE_HOOKS:
            try:
                await _PURGE_HOOKS[normalized_type](self.session, item.entity_id)
            except Exception as err:
                logger.warning(
                    f"Purge hook failed for {item.entity_type}:{item.entity_id}: {err}"
                )

        model = self._resolve_model(item.entity_type)
        if model is not None:
            pk_col = inspect(model).primary_key[0]
            stmt = delete(model).where(pk_col == item.entity_id)
            await self.session.execute(stmt)

        await self.repository.delete(item.id)

        actor_uuid = None
        if user_id:
            try:
                actor_uuid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                actor_uuid = None

        await self._emit_audit(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            action="PERMANENT_DELETE",
            entity_name=item.name,
            actor_id=actor_uuid,
            changes={"status": {"old": RecordStatus.TRASHED.value, "new": "PURGED"}},
            details=f"Permanently purged {item.name or item.entity_type} from trash",
        )
        return item

    async def _execute_bulk_action(
        self,
        ids: list[uuid.UUID],
        action: Callable[[uuid.UUID], Coroutine[Any, Any, Any]],
        action_verb: str,
    ) -> BulkResponse:
        """Execute a batch operation over trash IDs, logging individual failures."""
        count = 0
        for item_id in ids:
            try:
                await action(item_id)
                count += 1
            except Exception as err:
                logger.warning(f"Failed to {action_verb} trash item {item_id}: {err}")

        return BulkResponse(
            count=count,
            message=f"Successfully {action_verb}d {count} items from trash.",
        )

    async def bulk_restore(
        self,
        request: BulkTrashActionRequest,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> BulkResponse:
        """Restore multiple trash items in batch."""
        return await self._execute_bulk_action(
            request.ids,
            lambda item_id: self.restore_item(item_id, scope=scope, user_id=user_id),
            "restore",
        )

    async def bulk_purge(
        self,
        request: BulkTrashActionRequest,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> BulkResponse:
        """Purge multiple trash items in batch."""
        return await self._execute_bulk_action(
            request.ids,
            lambda item_id: self.purge_item(item_id, scope=scope, user_id=user_id),
            "purge",
        )

    async def purge_expired(self, limit: int = DEFAULT_TRASH_PURGE_LIMIT) -> int:
        """Purge all trash items whose retention period has expired."""
        now = datetime.now(UTC)
        expired_items = await self.repository.find_expired(cutoff=now, limit=limit)
        purged_count = 0

        for item in expired_items:
            try:
                await self.purge_item(item.id, scope=None)
                purged_count += 1
            except Exception as err:
                logger.error(f"Error purging expired trash item {item.id}: {err}")

        return purged_count
