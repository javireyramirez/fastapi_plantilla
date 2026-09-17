from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, time
from typing import Any, ClassVar, Final, NoReturn, Self
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import inspect, or_

from fastapi_plantilla.core.crud.actors import enrich_actors, to_uuid
from fastapi_plantilla.core.crud.exporter import format_export
from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import (
    DEFAULT_MAX_BULK_LIMIT,
    BulkIdsRequest,
    BulkResponse,
    ExportRequest,
    ListItemResponse,
    ListQueryParams,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    ScopeContext,
    SortOrder,
    UserReference,
    WriteOptions,
)
from fastapi_plantilla.core.database import Base

__all__ = [
    "APP_TIMEZONE",
    "BaseCRUDService",
    "ExportResult",
    "adjust_end_of_day",
    "normalize_filter_date",
    "to_uuid",
]

_MODEL_COLUMN_CACHE: dict[type[Any], frozenset[str]] = {}
APP_TIMEZONE: Final[ZoneInfo] = ZoneInfo("Europe/Madrid")


class ExportResult(tuple[Any, ...]):
    """3-tuple with optional export truncation metadata."""

    content: bytes | str
    media_type: str
    filename: str
    total_count: int
    is_truncated: bool

    def __new__(
        cls,
        content: bytes | str,
        media_type: str,
        filename: str,
        total_count: int = 0,
        is_truncated: bool = False,
    ) -> Self:
        """Create a new ExportResult instance."""
        instance = super().__new__(cls, (content, media_type, filename))
        instance.content = content
        instance.media_type = media_type
        instance.filename = filename
        instance.total_count = total_count
        instance.is_truncated = is_truncated
        return instance


def normalize_filter_date(
    dt: datetime | None,
    is_end_of_day: bool = False,
    default_tz: ZoneInfo = APP_TIMEZONE,
) -> datetime | None:
    """Normalize date parameter for DB filtering.

    If datetime is naive (no tzinfo), it is localized to default_tz (Europe/Madrid)
    and then converted to UTC.
    If is_end_of_day is True and time is midnight, adjusts to 23:59:59.999999
    in local tz before converting to UTC.
    """
    if dt is None:
        return None
    res = dt
    if is_end_of_day and res.time() == time.min:
        res = res.replace(hour=23, minute=59, second=59, microsecond=999999)
    if res.tzinfo is None:
        res = res.replace(tzinfo=default_tz)
    return res.astimezone(UTC)


def adjust_end_of_day(dt: datetime | None) -> datetime | None:
    """Adjust datetime to end-of-day (23:59:59.999999) if time is midnight."""
    if dt is not None and dt.time() == time.min:
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


class BaseCRUDService[ModelT: Base]:
    """Base business service orchestrating generic CRUD repository operations."""

    display_field: str = "name"
    resource_name: str = "Resource"
    mask_forbidden_as_not_found: bool = False
    MAX_BULK_LIMIT: int = DEFAULT_MAX_BULK_LIMIT
    export_schema: type[BaseModel] | None = None
    list_extra_fields: ClassVar[Sequence[str] | None] = None

    IMMUTABLE_FIELDS: frozenset[str] = frozenset(
        {
            "id",
            "created_at",
            "created_by",
            "deleted_at",
            "deleted_by",
            "restored_at",
            "restored_by",
            "version",
        }
    )

    IMMUTABLE_CREATE_FIELDS: frozenset[str] = frozenset(
        {
            "id",
            "created_at",
            "updated_at",
            "deleted_at",
            "deleted_by",
            "restored_at",
            "restored_by",
            "version",
        }
    )

    SENSITIVE_COLUMNS: frozenset[str] = frozenset(
        {
            "hashed_password",
            "password",
            "password_hash",
            "secret",
            "token",
            "totp_secret",
            "reset_token",
            "verification_token",
        }
    )

    def __init__(self, repository: BaseRepository[ModelT]) -> None:
        self.repository = repository
        self.model = repository.model
        if self.model not in _MODEL_COLUMN_CACHE:
            mapper = inspect(self.model)
            _MODEL_COLUMN_CACHE[self.model] = (
                frozenset(mapper.columns.keys())
                if mapper is not None and hasattr(mapper, "columns")
                else frozenset()
            )
        self._column_names: frozenset[str] = _MODEL_COLUMN_CACHE[self.model]

    # ==========================================
    # 1. HELPERS DE SEGURIDAD Y CONVERSIÓN
    # ==========================================

    _to_uuid = staticmethod(to_uuid)

    @staticmethod
    def _resolve_write_options(
        options: WriteOptions | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> WriteOptions:
        """Resolve WriteOptions as SSOT, merging fallback arguments."""
        if options is not None:
            if user_id is not None and options.user_id is None:
                options = options.model_copy(update={"user_id": user_id})
            if scope is not None and options.scope is None:
                options = options.model_copy(update={"scope": scope})
            return options
        return WriteOptions(user_id=user_id, scope=scope)

    @staticmethod
    def _extract_known_actor(opts: WriteOptions | None) -> UserReference | None:
        """Extract a UserReference from WriteOptions if actor details are present."""
        if opts and opts.user_id is not None and (opts.actor_name or opts.actor_email):
            uid = BaseCRUDService._to_uuid(opts.user_id)
            return UserReference(id=uid, name=opts.actor_name, email=opts.actor_email)
        return None

    def _get_column(self, field_name: str) -> Any | None:
        """Safely resolve an attribute to a mapped database column."""
        if (
            not field_name
            or field_name.startswith("_")
            or field_name.lower() in self.SENSITIVE_COLUMNS
            or field_name not in self._column_names
        ):
            return None
        return getattr(self.model, field_name, None)

    async def _raise_not_found_or_forbidden(
        self,
        id: uuid.UUID,
        detail_forbidden: str = "Forbidden: insufficient permissions for this record",
    ) -> NoReturn:
        """Raise 404 or 403, honoring anti-enumeration policy."""
        if self.mask_forbidden_as_not_found or not await self.repository.exists(
            self.repository.pk == id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{self.resource_name} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail_forbidden,
        )

    # ==========================================
    # 2. HELPERS DE FILTRADO Y ORDENACIÓN
    # ==========================================

    def build_string_filter(self, field_name: str, value: str | None) -> Any | None:
        """Build case-insensitive search clause with automatic wildcard escaping."""
        col = self._get_column(field_name)
        return (
            col.icontains(value, autoescape=True) if value and col is not None else None
        )

    def build_multi_search_filter(
        self, fields: list[str], search: str | None
    ) -> Any | None:
        """Build an OR search clause across multiple text columns."""
        if not search or not fields:
            return None
        clauses = [
            col.icontains(search, autoescape=True)
            for f in fields
            if (col := self._get_column(f)) is not None
        ]
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else or_(*clauses)

    def build_date_range_filter(
        self,
        field_name: str,
        from_date: datetime | None,
        to_date: datetime | None,
    ) -> list[Any]:
        """Build date range clauses with automatic end-of-day time adjustment."""
        col = self._get_column(field_name)
        if col is None:
            return []
        clauses: list[Any] = []
        if from_date is not None:
            clauses.append(col >= normalize_filter_date(from_date, is_end_of_day=False))
        if to_date is not None:
            clauses.append(col <= normalize_filter_date(to_date, is_end_of_day=True))
        return clauses

    def build_number_range_filter(
        self,
        field_name: str,
        min_val: float | None,
        max_val: float | None,
    ) -> list[Any]:
        """Build numeric range comparison clauses (>= min_val and <= max_val)."""
        col = self._get_column(field_name)
        if col is None:
            return []
        clauses: list[Any] = []
        if min_val is not None:
            clauses.append(col >= min_val)
        if max_val is not None:
            clauses.append(col <= max_val)
        return clauses

    def build_in_filter(self, field_name: str, values: list[Any] | None) -> Any | None:
        """Build IN clause if column exists with anti-DoS parameter limit."""
        if not values or len(values) > 1000:
            return None
        col = self._get_column(field_name)
        return col.in_(values) if col is not None else None

    def build_boolean_filter(
        self, field_name: str, value: bool | str | None
    ) -> Any | None:
        """Build boolean equality clause supporting bool and string representations."""
        col = self._get_column(field_name)
        if value is None or col is None:
            return None
        bool_val = (
            value if isinstance(value, bool) else value.lower() in ("true", "1", "yes")
        )
        return col == bool_val

    def build_null_filter(self, field_name: str, is_null: bool | None) -> Any | None:
        """Build IS NULL or IS NOT NULL clause."""
        col = self._get_column(field_name)
        if is_null is None or col is None:
            return None
        return col.is_(None) if is_null else col.is_not(None)

    def build_exact_filter(self, field_name: str, value: Any) -> Any | None:
        """Build exact equality clause if column exists and value is not None."""
        col = self._get_column(field_name)
        return col == value if value is not None and col is not None else None

    def get_available_sorts(self) -> dict[str, Any]:
        """Return custom sorting mappings overridable by business modules."""
        return {}

    def build_order_by(
        self,
        sort_by: str | None,
        sort_order: SortOrder = SortOrder.DESC,
        explicit_order: Any = None,
    ) -> Any:
        """Resolve order by clause from argument, custom map, or model column."""
        if explicit_order is not None:
            return explicit_order
        if not sort_by:
            created_col = self._get_column("created_at")
            return created_col.desc() if created_col is not None else None
        available = self.get_available_sorts()
        if sort_by in available:
            return available[sort_by]
        col = self._get_column(sort_by)
        if col is None:
            snake_sort = re.sub(r"(?<!^)(?=[A-Z])", "_", sort_by).lower()
            if snake_sort in available:
                return available[snake_sort]
            col = self._get_column(snake_sort)
        return (
            (col.desc() if sort_order == SortOrder.DESC else col.asc())
            if col is not None
            else None
        )

    def build_scope_filters(self, scope: ScopeContext | None = None) -> list[Any]:
        """Hook for scope filtering. Base CRUD applies no scope restrictions."""
        return []

    search_fields: ClassVar[list[str]] = ["name"]

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query filter clauses from pagination parameters."""
        clauses: list[Any] = []
        if (
            search_clause := self.build_multi_search_filter(
                self.search_fields, params.search
            )
        ) is not None:
            clauses.append(search_clause)

        for col_name, f_from, f_to in (
            ("created_at", params.created_at_from, params.created_at_to),
            ("updated_at", params.updated_at_from, params.updated_at_to),
            ("deleted_at", params.deleted_at_from, params.deleted_at_to),
            ("restored_at", params.restored_at_from, params.restored_at_to),
        ):
            if self._get_column(col_name) is not None:
                clauses.extend(self.build_date_range_filter(col_name, f_from, f_to))

        return clauses

    # ==========================================
    # 3. LECTURAS
    # ==========================================

    async def get_by_id(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Retrieve a single record by primary key ID or raise 404."""
        where_clauses = list(where) + self.build_scope_filters(scope)
        if not where_clauses:
            item = await self.repository.get_by_id(id)
        else:
            item = await self.repository.find_first(
                self.repository.pk == id, *where_clauses
            )
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{self.resource_name} not found",
            )
        await enrich_actors(self.repository.session, [item])
        return item

    async def find_paginated(
        self,
        params: PaginationParams,
        *where: Any,
        scope: ScopeContext | None = None,
        order_by: Any = None,
    ) -> PaginatedResponse[ModelT]:
        """Fetch a paginated list of records with filtering, sorting, and metadata."""
        skip = (params.page - 1) * params.limit
        order_clause = self.build_order_by(params.sort_by, params.sort_order, order_by)
        where_clauses = (
            list(where)
            + self.build_where_filters(params)
            + self.build_scope_filters(scope)
        )

        items, total = await self.repository.find_many_with_count(
            *where_clauses, skip=skip, limit=params.limit, order_by=order_clause
        )
        await enrich_actors(self.repository.session, items)
        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=items, meta=meta)

    async def find_list(
        self,
        params: ListQueryParams,
        *where: Any,
        scope: ScopeContext | None = None,
        display_field: str | None = None,
        extra_fields: Sequence[str] | None = None,
    ) -> list[ListItemResponse]:
        """Fetch a lightweight list of items for select/combobox dropdowns."""
        target_field = display_field or self.display_field
        if self._get_column(target_field) is None:
            target_field = self.repository.pk.name

        sort_by = (
            target_field
            if params.sort_by == "name" and self._get_column("name") is None
            else params.sort_by
        )
        order_clause = self.build_order_by(sort_by, params.sort_order)

        where_clauses = list(where) + self.build_scope_filters(scope)
        search_clause = self.build_string_filter(target_field, params.search)
        if search_clause is not None:
            where_clauses.append(search_clause)

        items = await self.repository.find_many(
            *where_clauses, limit=params.limit, order_by=order_clause
        )
        pk_name = self.repository.pk.name
        effective_extra = (
            extra_fields if extra_fields is not None else self.list_extra_fields
        )

        def _resolve_name(it: Any) -> str:
            val = getattr(it, target_field, None)
            if val is not None and str(val).strip():
                return str(val)
            pk_val = getattr(it, pk_name, None)
            return str(pk_val) if pk_val is not None else ""

        return [
            ListItemResponse(
                id=getattr(item, pk_name),
                name=_resolve_name(item),
                extra=(
                    {k: getattr(item, k) for k in effective_extra if hasattr(item, k)}
                    if effective_extra
                    else None
                ),
            )
            for item in items
        ]

    def get_status_filter(self, is_trash: bool) -> Any | None:
        """Hook for status filtering. Base CRUD applies no status filter."""
        return None

    export_limit: int = 1000

    async def export_data(
        self,
        req: ExportRequest,
        *where: Any,
        scope: ScopeContext | None = None,
        export_schema: type[BaseModel] | None = None,
        pagination_params_class: type[PaginationParams] | None = None,
    ) -> ExportResult:
        """Export records matching filters or IDs to CSV, Excel, or JSON format."""
        where_clauses: list[Any] = list(where) + self.build_scope_filters(scope)
        status_filter = self.get_status_filter(req.is_trash)
        if status_filter is not None:
            where_clauses.append(status_filter)

        effective_schema = export_schema or self.export_schema
        effective_param_cls: type[PaginationParams] = (
            pagination_params_class
            or getattr(self, "pagination_params_class", None)
            or PaginationParams
        )

        if req.ids:
            where_clauses.append(self.repository.pk.in_(req.ids))
        elif req.filters:
            param_cls: type[PaginationParams] = effective_param_cls
            valid_fields = {
                k: v
                for k, v in req.filters.items()
                if (hasattr(param_cls, k) or hasattr(PaginationParams, k))
                and v is not None
            }
            if valid_fields:
                try:
                    filter_params = param_cls(is_trash=req.is_trash, **valid_fields)
                except Exception:
                    fallback_fields = {
                        k: v
                        for k, v in valid_fields.items()
                        if hasattr(PaginationParams, k)
                    }
                    filter_params = PaginationParams(
                        is_trash=req.is_trash, **fallback_fields
                    )
                where_clauses.extend(self.build_where_filters(filter_params))

        order_clause = self.build_order_by(req.sort_by, req.sort_order)
        items = await self.repository.find_many(
            *where_clauses,
            limit=self.export_limit + 1,
            order_by=order_clause,
        )

        is_truncated = len(items) > self.export_limit
        if is_truncated:
            items = items[: self.export_limit]
            total_count = await self.repository.count(*where_clauses)
        else:
            total_count = len(items)

        excluded = self.SENSITIVE_COLUMNS | {"version"}
        rows, allowed_columns = self._serialize_export_rows(
            items, effective_schema, excluded
        )

        export_columns = (
            [c for c in req.columns if c in allowed_columns and c not in excluded]
            if req.columns is not None
            else None
        )
        if export_columns is None and not rows:
            export_columns = [c for c in allowed_columns if c not in excluded]

        slug = getattr(self, "resource_name", "export").lower()
        content, media_type, filename = format_export(
            format=req.format,
            data=rows,
            slug=slug,
            columns=export_columns,
        )
        return ExportResult(
            content=content,
            media_type=media_type,
            filename=filename,
            total_count=total_count,
            is_truncated=is_truncated,
        )

    def _serialize_export_rows(
        self,
        items: Sequence[Any],
        effective_schema: type[BaseModel] | None,
        excluded: frozenset[str] | set[str],
    ) -> tuple[list[dict[str, Any]], set[str] | frozenset[str]]:
        """Serialize model items into dictionaries and determine allowed columns."""
        rows: list[dict[str, Any]] = []
        if effective_schema is not None:
            for item in items:
                dumped = effective_schema.model_validate(item).model_dump(mode="json")
                rows.append({k: v for k, v in dumped.items() if k not in excluded})
            allowed_columns: set[str] | frozenset[str] = (
                set(effective_schema.model_fields.keys())
                if hasattr(effective_schema, "model_fields")
                else (set(rows[0].keys()) if rows else set())
            )
        else:
            for item in items:
                mapper = inspect(item.__class__)
                row = {
                    col.key: getattr(item, col.key)
                    for col in mapper.columns
                    if col.key not in excluded and not col.key.startswith("_")
                }
                rows.append(row)
            allowed_columns = self._column_names
        return rows, allowed_columns

    # ==========================================
    # 4. ESCRITURAS
    # ==========================================

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Create and persist a new record."""
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        if not allow_immutable:
            payload = {
                k: v
                for k, v in payload.items()
                if k not in self.IMMUTABLE_CREATE_FIELDS
            }
        opts = self._resolve_write_options(options, user_id, scope)
        item = await self.repository.create(payload)
        known = self._extract_known_actor(opts)
        await enrich_actors(
            self.repository.session, [item], known_users=[known] if known else None
        )
        return item

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Update and persist a record with optimistic concurrency check."""
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        if not allow_immutable:
            payload = {
                k: v for k, v in payload.items() if k not in self.IMMUTABLE_FIELDS
            }
        opts = self._resolve_write_options(options, user_id, scope)
        where_clauses = list(where) + self.build_scope_filters(opts.scope)
        known = self._extract_known_actor(opts)

        if not payload:
            item = await self.repository.find_first(
                self.repository.pk == id, *where_clauses
            )
            if item is None:
                if not await self.repository.exists(self.repository.pk == id):
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"{self.resource_name} not found",
                    )
                await self._raise_not_found_or_forbidden(id)
            if (
                expected_version is not None
                and hasattr(item, "version")
                and item.version != expected_version
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Concurrent modification conflict: "
                        "record was modified by another transaction"
                    ),
                )
            await enrich_actors(
                self.repository.session, [item], known_users=[known] if known else None
            )
            return item

        updated = await self.repository.update(
            id, payload, *where_clauses, expected_version=expected_version
        )
        if updated is None:
            if not await self.repository.exists(self.repository.pk == id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{self.resource_name} not found",
                )
            if where_clauses and not await self.repository.exists(
                self.repository.pk == id, *where_clauses
            ):
                await self._raise_not_found_or_forbidden(id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Concurrent modification conflict: "
                    "record was modified by another transaction"
                ),
            )
        await enrich_actors(
            self.repository.session, [updated], known_users=[known] if known else None
        )
        return updated

    async def delete(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Physically delete a record by ID or raise 404/403."""
        opts = self._resolve_write_options(options, user_id, scope)
        where_clauses = list(where) + self.build_scope_filters(opts.scope)
        deleted = await self.repository.delete(id, *where_clauses)
        if deleted is None:
            await self._raise_not_found_or_forbidden(id)
        known = self._extract_known_actor(opts)
        await enrich_actors(
            self.repository.session, [deleted], known_users=[known] if known else None
        )
        return deleted

    async def bulk_create(
        self,
        items: Sequence[BaseModel | dict[str, Any]],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk create multiple records with anti-DoS size limitation."""
        if not items:
            return BulkResponse(count=0, message="Successfully created 0 records")
        if len(items) > self.MAX_BULK_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Bulk creation exceeds limit of {self.MAX_BULK_LIMIT} items",
            )
        payload = [
            (
                {
                    k: v
                    for k, v in (
                        data.model_dump() if isinstance(data, BaseModel) else dict(data)
                    ).items()
                    if allow_immutable or k not in self.IMMUTABLE_CREATE_FIELDS
                }
            )
            for data in items
        ]
        count = await self.repository.create_many(data_list=payload)
        return BulkResponse(
            count=count, message=f"Successfully created {count} records"
        )

    async def bulk_delete(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Physically delete multiple records by their IDs."""
        opts = self._resolve_write_options(options, user_id, scope)
        where_clauses = list(where) + self.build_scope_filters(opts.scope)
        count = await self.repository.delete_many(req.ids, *where_clauses)
        return BulkResponse(
            count=count, message=f"Successfully deleted {count} records"
        )
