import uuid
from collections.abc import Callable
from enum import Enum
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Response, status
from pydantic import BaseModel

from fastapi_plantilla.core.crud.dependencies import get_scope_context
from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    ExportRequest,
    ListItemResponse,
    ListQueryParams,
    PaginatedResponse,
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service import BaseAuditService, BaseCRUDService
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["create_crud_router"]


def _build_write_options(
    current_user: Any, scope: ScopeContext | None = None
) -> WriteOptions:
    user_id = getattr(current_user, "id", None) or getattr(scope, "user_id", None)
    return WriteOptions(
        user_id=user_id,
        actor_name=getattr(current_user, "name", None),
        actor_email=getattr(current_user, "email", None),
        scope=scope,
    )


def create_crud_router[  # noqa: C901
    ModelT: Base,
    SchemaT: BaseModel,
    CreateSchemaT: BaseModel,
    UpdateSchemaT: BaseModel,
](
    service_getter: Callable[..., Any],
    schema_out: type[SchemaT],
    schema_create: type[CreateSchemaT],
    schema_update: type[UpdateSchemaT],
    prefix: str = "",
    tags: list[str | Enum] | None = None,
    resource_name: str | None = None,
    pagination_params: type[PaginationParams] = PaginationParams,
    max_bulk_limit: int = BaseCRUDService.MAX_BULK_LIMIT,
    current_user_getter: Callable[..., Any] = get_current_user,
    scope_getter: Callable[..., Any] = get_scope_context,
) -> APIRouter:
    """Dynamically generate standard CRUD endpoints for a domain resource."""
    router = APIRouter(prefix=prefix, tags=tags)

    def _scope_dep(action: RbacActions) -> Any:
        if resource_name is not None:
            # Deferred import to break circular import cycle between core.crud and rbac
            from fastapi_plantilla.modules.rbac.dependencies import (  # noqa: PLC0415
                require_permission,
            )

            return require_permission(resource_name, action)
        return scope_getter

    # ==========================================
    # 1. SIMPLE OPERATIONS (GET / LIST / EXPORT / CREATE)
    # ==========================================

    @router.get(
        "",
        response_model=PaginatedResponse[schema_out],  # type: ignore[valid-type]
        summary=f"List {schema_out.__name__} records",
    )
    @router.get(
        "/",
        response_model=PaginatedResponse[schema_out],  # type: ignore[valid-type]
        include_in_schema=False,
    )
    async def find_paginated(
        params: PaginationParams = Depends(pagination_params),
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.READ)),
    ) -> Any:
        return await service.find_paginated(params=params, scope=scope)

    @router.get(
        "/list",
        response_model=list[ListItemResponse],
        summary=f"List {schema_out.__name__} dropdown options",
    )
    async def find_list(
        params: ListQueryParams = Depends(),
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.READ)),
    ) -> Any:
        return await service.find_list(params=params, scope=scope)

    @router.post(
        "/export",
        response_class=Response,
        summary=f"Export {schema_out.__name__} records",
    )
    async def export_data(
        req: ExportRequest,
        service: Any = Depends(service_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.EXPORT)),
    ) -> Response:
        content, media_type, filename = await service.export_data(req, scope=scope)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post(
        "",
        response_model=schema_out,
        status_code=status.HTTP_201_CREATED,
        summary=f"Create {schema_out.__name__}",
    )
    @router.post(
        "/",
        response_model=schema_out,
        status_code=status.HTTP_201_CREATED,
        include_in_schema=False,
    )
    async def create(
        data: schema_create,  # type: ignore[valid-type]
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.CREATE)),
    ) -> Any:
        options = _build_write_options(current_user, scope)
        return await service.create(
            data=data, user_id=options.user_id, scope=scope, options=options
        )

    # ==========================================
    # 2. BULK OPERATIONS (Static paths before /{id})
    # ==========================================

    @router.post(
        "/bulk",
        response_model=BulkResponse,
        status_code=status.HTTP_201_CREATED,
        summary=f"Bulk create {schema_out.__name__} records",
    )
    async def bulk_create(
        items: list[schema_create] = Body(  # type: ignore[valid-type]
            ..., max_length=max_bulk_limit
        ),
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.CREATE)),
    ) -> BulkResponse:
        options = _build_write_options(current_user, scope)
        return await service.bulk_create(
            items=items, user_id=options.user_id, scope=scope, options=options
        )

    @router.post(
        "/bulk/trash",
        response_model=BulkResponse,
        summary=f"Bulk move {schema_out.__name__} records to trash",
    )
    async def bulk_trash(
        req: BulkIdsRequest,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
    ) -> BulkResponse:
        user_id = getattr(current_user, "id", None) or getattr(scope, "user_id", None)
        return await service.bulk_trash(req=req, user_id=user_id, scope=scope)

    @router.post(
        "/bulk/restore",
        response_model=BulkResponse,
        summary=f"Bulk restore {schema_out.__name__} records from trash",
    )
    async def bulk_restore(
        req: BulkIdsRequest,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.RESTORE)),
    ) -> BulkResponse:
        user_id = getattr(current_user, "id", None) or getattr(scope, "user_id", None)
        return await service.bulk_restore(req=req, user_id=user_id, scope=scope)

    @router.delete(
        "/bulk/permanent",
        response_model=BulkResponse,
        summary=f"Bulk permanently delete {schema_out.__name__} records from trash",
    )
    @router.post(
        "/bulk/permanent",
        response_model=BulkResponse,
        include_in_schema=False,
    )
    async def bulk_permanent_delete(
        req: BulkIdsRequest,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
    ) -> BulkResponse:
        options = _build_write_options(current_user, scope)
        return await service.bulk_permanent_delete(
            req=req, scope=scope, options=options
        )

    # ==========================================
    # 3. SINGLE RECORD OPERATIONS (Parameterized by ID)
    # ==========================================

    @router.get(
        "/{id}",
        response_model=schema_out,
        summary=f"Get {schema_out.__name__} by ID",
    )
    async def get_by_id(
        id: uuid.UUID,
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.READ)),
    ) -> Any:
        return await service.get_by_id(id, scope=scope)

    @router.patch(
        "/{id}",
        response_model=schema_out,
        summary=f"Update {schema_out.__name__}",
    )
    async def update(
        id: uuid.UUID,
        data: schema_update,  # type: ignore[valid-type]
        if_match: str | None = Header(default=None, alias="If-Match"),
        expected_version: int | None = None,
        service: BaseCRUDService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.UPDATE)),
    ) -> Any:
        resolved_version = expected_version
        if resolved_version is None and if_match:
            cleaned = if_match.strip().removeprefix("W/").strip('"').strip("'")
            if cleaned.isdigit():
                resolved_version = int(cleaned)
        if resolved_version is None and hasattr(data, "version"):
            data_version = getattr(data, "version", None)
            if isinstance(data_version, int):
                resolved_version = data_version

        options = _build_write_options(current_user, scope)
        return await service.update(
            id=id,
            data=data,
            expected_version=resolved_version,
            user_id=options.user_id,
            scope=scope,
            options=options,
        )

    @router.delete(
        "/{id}",
        response_model=schema_out,
        summary=f"Move {schema_out.__name__} to trash",
    )
    async def delete(
        id: uuid.UUID,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
    ) -> Any:
        options = _build_write_options(current_user, scope)
        return await service.delete(
            id=id, user_id=options.user_id, scope=scope, options=options
        )

    @router.post(
        "/{id}/restore",
        response_model=schema_out,
        summary=f"Restore {schema_out.__name__} from trash",
    )
    async def restore(
        id: uuid.UUID,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.RESTORE)),
    ) -> Any:
        options = _build_write_options(current_user, scope)
        return await service.restore(
            id=id, user_id=options.user_id, scope=scope, options=options
        )

    @router.delete(
        "/{id}/permanent",
        response_model=schema_out,
        summary=f"Permanently delete {schema_out.__name__} from trash",
    )
    async def permanent_delete(
        id: uuid.UUID,
        service: BaseAuditService[ModelT] = Depends(service_getter),
        current_user: Any = Depends(current_user_getter),
        scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
    ) -> Any:
        options = _build_write_options(current_user, scope)
        return await service.permanent_delete(id=id, scope=scope, options=options)

    return router
