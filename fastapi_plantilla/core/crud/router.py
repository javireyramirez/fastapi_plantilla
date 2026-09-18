import re
import uuid
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.dependencies import (
    build_write_options,
    get_scope_context,
)
from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    ExportRequest,
    ImportFormat,
    ImportJobPayload,
    ImportMode,
    ListItemResponse,
    ListQueryParams,
    PaginatedResponse,
    PaginationParams,
    ScopeContext,
)
from fastapi_plantilla.core.crud.service import BaseCRUDService
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["create_crud_router", "parse_if_match_version"]


def parse_if_match_version(if_match: str | None) -> int | None:
    """Parse integer entity version from If-Match header value.

    Supports standard and weak ETags (e.g., '1', '"1"', 'W/"1"').
    """
    if not if_match:
        return None
    cleaned = if_match.strip()
    if cleaned.upper().startswith("W/"):
        cleaned = cleaned[2:]
    cleaned = cleaned.strip('"').strip("'").strip()
    return int(cleaned) if cleaned.isdigit() else None


def create_crud_router[  # noqa: C901, PLR0912, PLR0915
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
    schema_export: type[BaseModel] | None = None,
    schema_import: type[BaseModel] | None = None,
    current_user_getter: Callable[..., Any] = get_current_user,
    scope_getter: Callable[..., Any] = get_scope_context,
    permission_factory: Callable[[str, RbacActions], Any] | None = None,
    include_create: bool = True,
    include_read: bool = True,
    include_update: bool = True,
    include_delete: bool = True,
    include_trash: bool = True,
    include_bulk: bool = True,
    include_export: bool = True,
    include_import: bool = True,
    supported_actions: Sequence[RbacActions | str] | None = None,
    service_factory: Callable[[AsyncSession], Any] | None = None,
) -> APIRouter:
    """Dynamically generate standard CRUD endpoints for a domain resource."""
    router = APIRouter(prefix=prefix, tags=tags)
    effective_export_schema = schema_export or schema_out
    effective_import_schema = schema_import or schema_create

    # Resolve supported actions from catalog if not explicitly given
    actions_set: set[str] | None = None
    if supported_actions is not None:
        actions_set = {
            a.value if hasattr(a, "value") else str(a) for a in supported_actions
        }
    elif resource_name is not None:
        try:
            from fastapi_plantilla.modules.rbac.catalog import (  # noqa: PLC0415
                CORE_SYSTEM_MODULES,
            )

            for mod in CORE_SYSTEM_MODULES:
                if mod.get("code") == resource_name:
                    mod_acts = mod.get("supported_actions")
                    if mod_acts is not None:
                        actions_set = {
                            a.value if hasattr(a, "value") else str(a) for a in mod_acts
                        }
                    break
        except ImportError:
            pass

    can_create = include_create and (
        actions_set is None or RbacActions.CREATE.value in actions_set
    )
    can_read = include_read and (
        actions_set is None or RbacActions.READ.value in actions_set
    )
    can_update = include_update and (
        actions_set is None or RbacActions.UPDATE.value in actions_set
    )
    can_delete = include_delete and (
        actions_set is None or RbacActions.DELETE.value in actions_set
    )
    can_restore = include_trash and (
        actions_set is None or RbacActions.RESTORE.value in actions_set
    )
    can_export = include_export and (
        actions_set is None or RbacActions.EXPORT.value in actions_set
    )
    can_import = include_import and (
        actions_set is None or RbacActions.IMPORT.value in actions_set
    )

    if service_factory is not None and resource_name is not None:
        from fastapi_plantilla.core.crud.importer import (  # noqa: PLC0415
            register_import_resource,
        )

        register_import_resource(
            resource_name=resource_name,
            schema_create=effective_import_schema,
            service_factory=service_factory,
        )

    if service_factory is not None and resource_name is not None and can_export:
        from fastapi_plantilla.core.crud.export_job import (  # noqa: PLC0415, F401
            handle_exports_generate,
        )
        from fastapi_plantilla.core.crud.exporter import (  # noqa: PLC0415
            register_export_resource,
        )

        register_export_resource(
            resource_name=resource_name,
            service_factory=service_factory,
            export_schema=effective_export_schema,
            pagination_params_class=pagination_params,
        )

    def _scope_dep(action: RbacActions) -> Any:
        if permission_factory is not None and resource_name is not None:
            return permission_factory(resource_name, action)
        if resource_name is not None:
            # Deferred import to break circular import cycle between core.crud and rbac
            from fastapi_plantilla.modules.rbac.dependencies import (  # noqa: PLC0415
                require_permission,
            )

            return require_permission(resource_name, action)
        return scope_getter

    # ==========================================
    # 1. READ & EXPORT OPERATIONS
    # ==========================================

    if can_read:

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
            params: Annotated[pagination_params, Query()],  # type: ignore[valid-type]
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

    if can_export:
        from fastapi_plantilla.core.crud.exporter import (  # noqa: PLC0415
            dispatch_export_job,
            should_run_export_async,
        )

        @router.post(
            "/export",
            response_class=Response,
            summary=f"Export {effective_export_schema.__name__} records",
            responses={
                status.HTTP_200_OK: {"description": "Synchronous file download"},
                status.HTTP_202_ACCEPTED: {
                    "description": "Background export job accepted"
                },
            },
        )
        async def export_data(
            req: ExportRequest,
            request: Request,
            async_job: bool = Query(
                False,
                description="Trigger export asynchronously via background jobs worker",
            ),
            service: Any = Depends(service_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.EXPORT)),
        ) -> Response:
            res_name = resource_name or effective_export_schema.__name__
            if await should_run_export_async(
                async_job=async_job,
                request=request,
                service=service,
                req=req,
                scope=scope,
                pagination_params_class=pagination_params,
            ):
                job = await dispatch_export_job(
                    request=request,
                    service=service,
                    resource_name=res_name,
                    req=req,
                    scope=scope,
                )
                return Response(
                    content=job.model_dump_json(),
                    media_type="application/json",
                    status_code=status.HTTP_202_ACCEPTED,
                )

            result = await service.export_data(
                req,
                scope=scope,
                export_schema=effective_export_schema,
                pagination_params_class=pagination_params,
            )
            content, media_type, filename = result[0], result[1], result[2]
            headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
            total_count = getattr(result, "total_count", None)
            if total_count is not None:
                headers["X-Total-Count"] = str(total_count)
            if getattr(result, "is_truncated", False):
                headers["X-Export-Truncated"] = "true"
            return Response(
                content=content,
                media_type=media_type,
                headers=headers,
            )

    if can_import:
        from fastapi_plantilla.core.crud.importer import (  # noqa: PLC0415
            DEFAULT_MAX_IMPORT_FILE_BYTES,
            IMPORT_STORAGE_PREFIX,
            generate_import_template,
        )
        from fastapi_plantilla.modules.jobs.dependencies import (  # noqa: PLC0415
            get_job_service,
        )
        from fastapi_plantilla.modules.jobs.schema import (  # noqa: PLC0415
            JobCreateRequest,
            JobResponse,
        )
        from fastapi_plantilla.modules.storage.dependencies import (  # noqa: PLC0415
            get_storage_provider,
        )
        from fastapi_plantilla.modules.storage.providers.base import (  # noqa: PLC0415
            StorageProvider,
        )

        @router.get(
            "/import-template",
            response_class=Response,
            summary=f"Download import template for {effective_import_schema.__name__}",
        )
        async def download_import_template(
            format: ImportFormat = Query(
                ImportFormat.EXCEL, description="Template format (excel/csv)"
            ),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.IMPORT)),
        ) -> Response:
            content, media_type, filename = generate_import_template(
                schema=effective_import_schema,
                fmt=format,
                resource_name=resource_name or effective_import_schema.__name__,
            )
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @router.post(
            "/import",
            response_model=JobResponse,
            status_code=status.HTTP_202_ACCEPTED,
            summary=f"Import {effective_import_schema.__name__} records from file",
        )
        async def import_data(
            file: UploadFile = File(
                ..., description="Excel (.xlsx) or CSV file to import"
            ),
            mode: ImportMode = Form(
                ImportMode.ATOMIC, description="Transactional behavior"
            ),
            dry_run: bool = Form(
                False, description="Simulate validation without committing to DB"
            ),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.IMPORT)),
            job_service: Any = Depends(get_job_service),
            storage_provider: StorageProvider = Depends(get_storage_provider),
        ) -> JobResponse:
            raw_fn = file.filename or "import.csv"
            safe_fn = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(raw_fn).name)
            lower_fn = safe_fn.lower()
            if not lower_fn.endswith((".csv", ".xlsx")):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "Invalid file format. Only .csv and .xlsx files are supported."
                    ),
                )

            file_bytes = await file.read()
            if len(file_bytes) > DEFAULT_MAX_IMPORT_FILE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"File exceeds maximum allowed size of "
                        f"{DEFAULT_MAX_IMPORT_FILE_BYTES // (1024 * 1024)}MB."
                    ),
                )

            fmt = ImportFormat.CSV if lower_fn.endswith(".csv") else ImportFormat.EXCEL
            job_id = uuid.uuid4()
            storage_key = f"{IMPORT_STORAGE_PREFIX}/{job_id}_{safe_fn}"
            content_type = file.content_type or (
                "text/csv"
                if fmt == ImportFormat.CSV
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            try:
                await storage_provider.upload(
                    storage_key, file_bytes, content_type=content_type
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Storage upload failed: {exc}",
                ) from exc

            user_id = getattr(current_user, "id", None)
            scope_dict = (
                {
                    "scope": (
                        scope.scope.value
                        if hasattr(scope.scope, "value")
                        else str(scope.scope)
                    ),
                    "user_id": str(scope.user_id) if scope.user_id else None,
                    "team_ids": [str(t) for t in (scope.team_ids or [])],
                    "teammate_ids": [str(t) for t in (scope.teammate_ids or [])],
                    "is_super_admin": scope.is_super_admin,
                }
                if scope
                else None
            )

            job_req = JobCreateRequest(
                name="imports.validate",
                payload=ImportJobPayload(
                    resource_name=resource_name
                    or effective_import_schema.__name__.lower(),
                    storage_key=storage_key,
                    filename=safe_fn,
                    format=fmt,
                    mode=mode,
                    dry_run=dry_run,
                    user_id=user_id,
                    scope=scope_dict,
                ).model_dump(mode="json"),
                entity_type=resource_name,
            )
            return await job_service.enqueue(job_req, created_by_id=user_id)

    # ==========================================
    # 2. CREATE OPERATIONS
    # ==========================================

    if can_create:

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
            request: Request,
            service: BaseCRUDService[ModelT] = Depends(service_getter),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.CREATE)),
        ) -> Any:
            options = build_write_options(current_user, scope, request)
            return await service.create(
                data=data, user_id=options.user_id, scope=scope, options=options
            )

        if include_bulk:

            @router.post(
                "/bulk",
                response_model=BulkResponse,
                status_code=status.HTTP_201_CREATED,
                summary=f"Bulk create {schema_out.__name__} records",
            )
            async def bulk_create(
                request: Request,
                items: list[schema_create] = Body(  # type: ignore[valid-type]
                    ..., max_length=max_bulk_limit
                ),
                service: BaseCRUDService[ModelT] = Depends(service_getter),
                current_user: Any = Depends(current_user_getter),
                scope: ScopeContext = Depends(_scope_dep(RbacActions.CREATE)),
            ) -> BulkResponse:
                options = build_write_options(current_user, scope, request)
                return await service.bulk_create(
                    items=items, user_id=options.user_id, scope=scope, options=options
                )

    # ==========================================
    # 3. BULK TRASH / RESTORE / PERMANENT DELETE (Static paths before /{id})
    # ==========================================

    if include_bulk:
        if can_delete and can_restore:

            @router.post(
                "/bulk/trash",
                response_model=BulkResponse,
                summary=f"Bulk move {schema_out.__name__} records to trash",
            )
            async def bulk_trash(
                req: BulkIdsRequest,
                request: Request,
                service: Any = Depends(service_getter),
                current_user: Any = Depends(current_user_getter),
                scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
            ) -> BulkResponse:
                options = build_write_options(current_user, scope, request)
                return await service.bulk_trash(
                    req=req, user_id=options.user_id, scope=scope, options=options
                )

        if can_restore:

            @router.post(
                "/bulk/restore",
                response_model=BulkResponse,
                summary=f"Bulk restore {schema_out.__name__} records from trash",
            )
            async def bulk_restore(
                req: BulkIdsRequest,
                request: Request,
                service: Any = Depends(service_getter),
                current_user: Any = Depends(current_user_getter),
                scope: ScopeContext = Depends(_scope_dep(RbacActions.RESTORE)),
            ) -> BulkResponse:
                options = build_write_options(current_user, scope, request)
                return await service.bulk_restore(
                    req=req, user_id=options.user_id, scope=scope, options=options
                )

        if can_delete and can_restore:

            @router.delete(
                "/bulk/permanent",
                response_model=BulkResponse,
                summary=(
                    f"Bulk permanently delete {schema_out.__name__} records from trash"
                ),
            )
            @router.post(
                "/bulk/permanent",
                response_model=BulkResponse,
                include_in_schema=False,
            )
            async def bulk_permanent_delete(
                req: BulkIdsRequest,
                request: Request,
                service: Any = Depends(service_getter),
                current_user: Any = Depends(current_user_getter),
                scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
            ) -> BulkResponse:
                options = build_write_options(current_user, scope, request)
                return await service.bulk_permanent_delete(
                    req=req, scope=scope, options=options
                )

    # ==========================================
    # 4. SINGLE RECORD OPERATIONS (Parameterized by ID)
    # ==========================================

    if can_read:

        @router.get(
            "/{id}",
            response_model=schema_out,
            summary=f"Get {schema_out.__name__} by ID",
        )
        async def get_by_id(
            id: uuid.UUID,
            response: Response,
            service: BaseCRUDService[ModelT] = Depends(service_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.READ)),
        ) -> Any:
            item = await service.get_by_id(id, scope=scope)
            version = getattr(item, "version", None)
            if version is not None:
                response.headers["ETag"] = f'W/"{version}"'
            return item

    if can_update:

        @router.patch(
            "/{id}",
            response_model=schema_out,
            summary=f"Update {schema_out.__name__}",
        )
        async def update(
            id: uuid.UUID,
            data: schema_update,  # type: ignore[valid-type]
            request: Request,
            response: Response,
            if_match: str | None = Header(default=None, alias="If-Match"),
            expected_version: int | None = None,
            service: BaseCRUDService[ModelT] = Depends(service_getter),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.UPDATE)),
        ) -> Any:
            """Update record with optimistic locking.

            Version precedence:
            If-Match header > expected_version query param > data.version body field.
            """
            resolved_version = parse_if_match_version(if_match)
            if resolved_version is None:
                resolved_version = expected_version
            if resolved_version is None and hasattr(data, "version"):
                data_version = getattr(data, "version", None)
                if isinstance(data_version, int):
                    resolved_version = data_version

            options = build_write_options(current_user, scope, request)
            updated = await service.update(
                id=id,
                data=data,
                expected_version=resolved_version,
                user_id=options.user_id,
                scope=scope,
                options=options,
            )
            version = getattr(updated, "version", None)
            if version is not None:
                response.headers["ETag"] = f'W/"{version}"'
            return updated

    if can_delete:
        delete_summary = (
            f"Move {schema_out.__name__} to trash"
            if can_restore
            else f"Delete {schema_out.__name__}"
        )

        @router.delete(
            "/{id}",
            response_model=schema_out,
            summary=delete_summary,
        )
        async def delete(
            id: uuid.UUID,
            request: Request,
            service: Any = Depends(service_getter),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
        ) -> Any:
            options = build_write_options(current_user, scope, request)
            return await service.delete(
                id=id, user_id=options.user_id, scope=scope, options=options
            )

    if can_restore:

        @router.post(
            "/{id}/restore",
            response_model=schema_out,
            summary=f"Restore {schema_out.__name__} from trash",
        )
        async def restore(
            id: uuid.UUID,
            request: Request,
            service: Any = Depends(service_getter),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.RESTORE)),
        ) -> Any:
            options = build_write_options(current_user, scope, request)
            return await service.restore(
                id=id, user_id=options.user_id, scope=scope, options=options
            )

    if can_delete and can_restore:

        @router.delete(
            "/{id}/permanent",
            response_model=schema_out,
            summary=f"Permanently delete {schema_out.__name__} from trash",
        )
        async def permanent_delete(
            id: uuid.UUID,
            request: Request,
            service: Any = Depends(service_getter),
            current_user: Any = Depends(current_user_getter),
            scope: ScopeContext = Depends(_scope_dep(RbacActions.DELETE)),
        ) -> Any:
            options = build_write_options(current_user, scope, request)
            return await service.permanent_delete(id=id, scope=scope, options=options)

    return router
