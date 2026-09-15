import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse

from fastapi_plantilla.core.crud.dependencies import (
    get_scope_context,
    get_write_options,
)
from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    ScopeContext,
    WriteOptions,
)
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
    file: Annotated[UploadFile, File(...)],
    entity_type: Annotated[str, Form(max_length=50)],
    entity_id: Annotated[uuid.UUID, Form()],
    description: Annotated[str | None, Form()] = None,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Upload file directly to server and persist storage metadata."""
    file_bytes = await file.read()
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
    content, filename, mime = await service.download_content(id, scope=scope)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/zip",
    summary="Download multiple storage files packaged in a ZIP archive",
)
async def download_zip(
    request: ZipDownloadRequest,
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Response:
    """Package selected files into a ZIP archive and return payload."""
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
    params: Annotated[StorageFilterParams, Depends()],
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PaginatedResponse[Storage]:
    """Retrieve paginated list of storage records with optional criteria filters."""
    where = []
    if params.entity_id is not None:
        where.append(Storage.entity_id == params.entity_id)

    if params.entity_type:
        where.append(Storage.entity_type == params.entity_type)

    if params.is_uploaded is not None:
        where.append(Storage.is_uploaded == params.is_uploaded)

    if params.content_types:
        where.append(Storage.content_type.in_(params.content_types))

    if params.size_min is not None:
        where.append(Storage.size_bytes >= params.size_min)

    if params.size_max is not None:
        where.append(Storage.size_bytes <= params.size_max)

    return await service.find_paginated(params, *where, scope=scope)


@router.get(
    "/{id}",
    response_model=StorageResponse,
    summary="Retrieve single storage metadata by ID",
)
async def get_storage(
    id: uuid.UUID,
    service: StorageService = Depends(get_storage_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Storage:
    """Get storage details by primary key ID."""
    return await service.get_by_id(id, scope=scope)


@router.patch(
    "/{id}",
    response_model=StorageResponse,
    summary="Update storage metadata",
)
async def update_storage(
    id: uuid.UUID,
    data: StorageUpdateSchema,
    service: StorageService = Depends(get_storage_service),
    options: WriteOptions = Depends(get_write_options),
) -> Storage:
    """Update editable storage metadata fields."""
    return await service.update(
        id,
        data,
        user_id=options.user_id,
        scope=options.scope,
    )


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
