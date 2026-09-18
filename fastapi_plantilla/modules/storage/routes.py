import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
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
from fastapi.responses import RedirectResponse

from fastapi_plantilla.core.crud.dependencies import (
    get_scope_context,
    get_write_options,
)
from fastapi_plantilla.core.crud.router import parse_if_match_version
from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.modules.jobs.dependencies import get_job_service
from fastapi_plantilla.modules.jobs.schema import JobCreateRequest
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.storage.dependencies import get_storage_service
from fastapi_plantilla.modules.storage.local_routes import local_router
from fastapi_plantilla.modules.storage.models import Storage
from fastapi_plantilla.modules.storage.schema import (
    ConfirmUploadRequest,
    CreateExternalUrlRequest,
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
    StorageFilterParams,
    StorageResponse,
    StorageUpdateSchema,
    ZipDownloadRequest,
)
from fastapi_plantilla.modules.storage.service import StorageService

router = APIRouter(prefix="/storage", tags=["Storage"])


@router.post(
    "/presigned-upload",
    response_model=PresignedUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a presigned URL for direct client-to-bucket upload",
)
async def request_presigned_upload(
    data: PresignedUploadRequest,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> PresignedUploadResponse:
    """Register pending storage record and return presigned direct upload URL."""
    return await service.request_presigned_upload(data, options=options)


@router.post(
    "/{id}/confirm",
    response_model=StorageResponse,
    summary="Confirm that direct upload to bucket has completed",
)
async def confirm_upload(
    id: uuid.UUID,
    data: ConfirmUploadRequest | None = None,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Validate file existence in storage and activate record."""
    return await service.confirm_upload(id, data=data, options=options)


@router.post(
    "/upload",
    response_model=StorageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Direct multipart file upload through application server",
)
async def upload_direct(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    entity_type: Annotated[str, Form(max_length=50)],
    entity_id: Annotated[uuid.UUID, Form()],
    description: Annotated[str | None, Form(max_length=1000)] = None,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Upload file directly to server and persist storage metadata."""
    max_size = await service.get_max_upload_size()

    # Early rejection: check file.size if known or Content-Length with multipart margin
    if file.size is not None and file.size > max_size:
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File size exceeds maximum limit of {max_mb:.1f} MB",
        )

    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > max_size + 65536
    ):
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File size exceeds maximum limit of {max_mb:.1f} MB",
        )

    # Safe bounded read: avoid unconstrained memory loading
    chunks: list[bytes] = []
    bytes_read = 0
    chunk_size = 64 * 1024  # 64 KB

    while chunk := await file.read(chunk_size):
        bytes_read += len(chunk)
        if bytes_read > max_size:
            max_mb = max_size / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File size exceeds maximum limit of {max_mb:.1f} MB",
            )
        chunks.append(chunk)

    file_bytes = b"".join(chunks)
    filename = file.filename or "uploaded_file"
    return await service.upload_direct(
        file_data=file_bytes,
        filename=filename,
        entity_type=entity_type,
        entity_id=entity_id,
        content_type=file.content_type,
        description=description,
        options=options,
    )


@router.post(
    "/url",
    response_model=StorageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register external link as storage resource (Drive, Dropbox, etc.)",
)
async def add_external_url(
    data: CreateExternalUrlRequest,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Register external URL resource and persist storage metadata."""
    return await service.create_external_url(data, options=options)


@router.delete(
    "/pending/purge",
    summary="Purge unconfirmed pending storage uploads older than retention window",
)
async def purge_pending_uploads(
    older_than_seconds: int | None = Query(default=None, ge=0),
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> dict[str, Any]:
    """Purge orphaned pending presigned uploads from database."""
    if not scope.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: only administrators can purge pending uploads.",
        )
    purged_count = await service.purge_pending_orphans(older_than_seconds)
    return {"purged": purged_count}


@router.get(
    "/{id}/download-url",
    response_model=PresignedDownloadResponse,
    summary="Generate a presigned download URL for a storage file",
)
async def get_presigned_download_url(
    id: uuid.UUID,
    expires_in: int = Query(default=3600, ge=60, le=86400),
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PresignedDownloadResponse:
    """Return temporary presigned URL for downloading storage file."""
    return await service.get_presigned_download(id, expires_in=expires_in, scope=scope)


@router.get(
    "/{id}/download",
    summary="Download file directly by streaming from storage provider",
)
async def download_file(
    id: uuid.UUID,
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Response:
    """Download file bytes directly or redirect to external resource URL."""
    record = await service.get_by_id(id, scope=scope)
    if record.external_url:
        return RedirectResponse(
            url=record.external_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
    content, filename, mime = await service.download_content(record, scope=scope)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/zip",
    summary="Download multiple storage files packaged in a ZIP archive",
    responses={
        status.HTTP_200_OK: {"description": "Synchronous ZIP file download"},
        status.HTTP_202_ACCEPTED: {
            "description": "Background ZIP compression job accepted"
        },
    },
)
async def download_zip(
    request: ZipDownloadRequest,
    async_job: bool = Query(
        False,
        description="Process ZIP compression asynchronously via background worker",
    ),
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
    job_service: JobService = Depends(get_job_service),
) -> Response:
    """Package selected files into a ZIP archive or enqueue background job."""
    should_async = async_job
    if service.settings_service:
        scope_filters = service.build_scope_filters(scope) if scope else []
        candidates = []
        if request.storage_ids:
            candidates = await service.storage_repo.find_uploaded_by_ids(
                request.storage_ids, scope_filters=scope_filters
            )
        elif request.entity_type and request.entity_id:
            max_zip_files = await service.get_max_zip_file_count()
            candidates = await service.storage_repo.find_by_entity(
                request.entity_type,
                request.entity_id,
                limit=max_zip_files,
                scope_filters=scope_filters,
            )

        if candidates:
            total_bytes = sum(c.size_bytes or 0 for c in candidates)
            max_zip_bytes = await service.get_max_zip_total_bytes()
            if total_bytes > max_zip_bytes:
                max_mb = max_zip_bytes / (1024 * 1024)
                actual_mb = total_bytes / (1024 * 1024)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"Total size of files to zip ({actual_mb:.1f} MB) "
                        f"exceeds maximum limit of {max_mb:.0f} MB"
                    ),
                )

            if not should_async:
                threshold_bytes = int(
                    await service.settings_service.get_value(
                        "storage.zip_async_threshold_bytes",
                        default=52428800,  # 50 MB
                    )
                )
                if total_bytes > threshold_bytes:
                    should_async = True

    if should_async:
        zip_filename = "storage.zip"
        if request.archive_name:
            clean_name = request.archive_name.strip()
            zip_filename = (
                clean_name
                if clean_name.lower().endswith(".zip")
                else f"{clean_name}.zip"
            )
        job_payload = {
            "storage_ids": [str(sid) for sid in request.storage_ids]
            if request.storage_ids
            else None,
            "entity_type": request.entity_type,
            "entity_id": str(request.entity_id) if request.entity_id else None,
            "zip_filename": zip_filename,
            "scope": {
                "scope": scope.scope.value,
                "user_id": str(scope.user_id) if scope.user_id else None,
                "teammate_ids": [str(t) for t in scope.teammate_ids]
                if scope.teammate_ids
                else [],
                "is_super_admin": scope.is_super_admin,
            },
        }
        job = await job_service.enqueue(
            JobCreateRequest(
                name="storage.compress",
                payload=job_payload,
                entity_type="storage",
            ),
            created_by_id=scope.user_id,
        )
        return Response(
            content=job.model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED,
        )

    zip_bytes, zip_filename = await service.download_zip(request, scope=scope)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@router.get(
    "",
    response_model=PaginatedResponse[StorageResponse],
    summary="List paginated storage records with filtering and RBAC scope",
)
async def list_storage(
    params: Annotated[StorageFilterParams, Query()],
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PaginatedResponse[Storage]:
    """Retrieve paginated list of storage records with optional criteria filters."""
    return await service.find_paginated(params, scope=scope)


@router.get(
    "/{id}",
    response_model=StorageResponse,
    summary="Retrieve single storage metadata by ID",
)
async def get_storage(
    id: uuid.UUID,
    response: Response,
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Storage:
    """Get storage details by primary key ID."""
    record = await service.get_by_id(id, scope=scope)
    response.headers["ETag"] = f'W/"{record.version}"'
    return record


@router.patch(
    "/{id}",
    response_model=StorageResponse,
    summary="Update storage metadata",
)
async def update_storage(
    id: uuid.UUID,
    data: StorageUpdateSchema,
    response: Response,
    expected_version: int | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Update editable storage metadata fields with optimistic concurrency."""
    parsed_version = parse_if_match_version(if_match)
    effective_version = (
        parsed_version if parsed_version is not None else expected_version
    )
    record = await service.update(
        id,
        data,
        expected_version=effective_version,
        user_id=options.user_id,
        scope=options.scope,
    )
    response.headers["ETag"] = f'W/"{record.version}"'
    return record


@router.delete(
    "/{id}",
    response_model=StorageResponse,
    summary="Soft-delete storage item to trash bin",
)
async def soft_delete_storage(
    id: uuid.UUID,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Move storage item to trash bin."""
    return await service.trash(
        id,
        user_id=options.user_id,
        scope=options.scope,
    )


@router.post(
    "/{id}/restore",
    response_model=StorageResponse,
    summary="Restore storage item from trash bin",
)
async def restore_storage(
    id: uuid.UUID,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Restore storage item from trash bin to active state."""
    return await service.restore(
        id,
        user_id=options.user_id,
        scope=options.scope,
    )


@router.delete(
    "/{id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete file from storage provider and database",
)
async def permanent_delete_storage(
    id: uuid.UUID,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> None:
    """Permanently delete file from storage provider and purge database record."""
    await service.permanent_delete_storage(id, options=options)


router.include_router(local_router)
