import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from fastapi_plantilla.core.crud.dependencies import (
    get_scope_context,
    get_write_options,
)
from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.modules.storage.dependencies import get_document_service
from fastapi_plantilla.modules.storage.local_routes import local_router
from fastapi_plantilla.modules.storage.models import Document
from fastapi_plantilla.modules.storage.schema import (
    ConfirmUploadRequest,
    DocumentFilterParams,
    DocumentResponse,
    DocumentUpdateSchema,
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
    ZipDownloadRequest,
)
from fastapi_plantilla.modules.storage.service import DocumentService

router = APIRouter(prefix="/storage", tags=["Storage"])


@router.post(
    "/documents/presigned-upload",
    response_model=PresignedUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a presigned URL for direct client-to-bucket upload",
)
async def request_presigned_upload(
    data: PresignedUploadRequest,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> PresignedUploadResponse:
    """Register pending document and return presigned direct upload URL."""
    return await service.request_presigned_upload(data, options=options)


@router.post(
    "/documents/{id}/confirm",
    response_model=DocumentResponse,
    summary="Confirm that direct upload to bucket has completed",
)
async def confirm_upload(
    id: uuid.UUID,
    data: ConfirmUploadRequest | None = None,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> Document:
    """Validate file existence in storage and activate document record."""
    return await service.confirm_upload(id, data=data, options=options)


@router.post(
    "/documents/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Direct multipart file upload through application server",
)
async def upload_direct(
    file: Annotated[UploadFile, File(...)],
    entity_type: Annotated[str | None, Form(alias="entity_type", max_length=50)] = None,
    entity_type_camel: Annotated[
        str | None, Form(alias="entityType", max_length=50)
    ] = None,
    entity_id: Annotated[uuid.UUID | None, Form(alias="entity_id")] = None,
    entity_id_camel: Annotated[uuid.UUID | None, Form(alias="entityId")] = None,
    description: Annotated[str | None, Form()] = None,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> Document:
    """Upload file directly to server and persist document metadata."""
    resolved_type = entity_type or entity_type_camel
    resolved_id = entity_id or entity_id_camel
    if not resolved_type or not resolved_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity_type and entity_id are required fields.",
        )

    file_bytes = await file.read()
    filename = file.filename or "uploaded_file"
    return await service.upload_direct(
        file_data=file_bytes,
        filename=filename,
        entity_type=resolved_type,
        entity_id=resolved_id,
        content_type=file.content_type,
        description=description,
        options=options,
    )


@router.get(
    "/documents/{id}/download-url",
    response_model=PresignedDownloadResponse,
    summary="Generate a presigned download URL for a document",
)
async def get_presigned_download_url(
    id: uuid.UUID,
    expires_in: int = Query(default=3600, ge=60, le=86400),
    service: DocumentService = Depends(get_document_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PresignedDownloadResponse:
    """Return temporary presigned URL for downloading document file."""
    return await service.get_presigned_download(id, expires_in=expires_in, scope=scope)


@router.get(
    "/documents/{id}/download",
    summary="Download document file directly by streaming from storage",
)
async def download_file(
    id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Response:
    """Download document bytes directly from storage provider."""
    content, filename, mime = await service.download_content(id, scope=scope)
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/documents/zip",
    summary="Download multiple documents packaged in a ZIP archive",
)
async def download_zip(
    request: ZipDownloadRequest,
    service: DocumentService = Depends(get_document_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Response:
    """Package selected documents into a ZIP archive and return payload."""
    zip_bytes, zip_filename = await service.download_zip(request, scope=scope)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@router.get(
    "/documents",
    response_model=PaginatedResponse[DocumentResponse],
    summary="List paginated documents with filtering and RBAC scope",
)
async def list_documents(
    params: Annotated[DocumentFilterParams, Depends()],
    service: DocumentService = Depends(get_document_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PaginatedResponse[Document]:
    """Retrieve paginated list of documents with optional criteria filters."""
    where = []
    if params.target_entity_id is not None:
        where.append(Document.entity_id == params.target_entity_id)

    if params.target_entity_type:
        candidates = [params.target_entity_type]
        norm = params.target_entity_type.strip().lower()
        if norm in ("company", "companies"):
            candidates = ["company", "companies"]
        elif norm in ("user", "users"):
            candidates = ["user", "users"]
        elif norm in ("team", "teams"):
            candidates = ["team", "teams"]
        where.append(Document.entity_type.in_(candidates))

    if params.target_is_uploaded is not None:
        where.append(Document.is_uploaded == params.target_is_uploaded)

    return await service.find_paginated(params, *where, scope=scope)


@router.get(
    "/documents/{id}",
    response_model=DocumentResponse,
    summary="Retrieve single document metadata by ID",
)
async def get_document(
    id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> Document:
    """Get document details by primary key ID."""
    return await service.get_by_id(id, scope=scope)


@router.patch(
    "/documents/{id}",
    response_model=DocumentResponse,
    summary="Update document metadata",
)
async def update_document(
    id: uuid.UUID,
    data: DocumentUpdateSchema,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> Document:
    """Update editable document metadata fields."""
    return await service.update(
        id,
        data,
        user_id=options.user_id,
        scope=options.scope,
    )


@router.delete(
    "/documents/{id}",
    response_model=DocumentResponse,
    summary="Soft-delete document to trash bin",
)
async def soft_delete_document(
    id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> Document:
    """Move document to trash bin."""
    return await service.trash(
        id,
        user_id=options.user_id,
        scope=options.scope,
    )


@router.post(
    "/documents/{id}/restore",
    response_model=DocumentResponse,
    summary="Restore document from trash bin",
)
async def restore_document(
    id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> Document:
    """Restore document from trash bin to active state."""
    return await service.restore(
        id,
        user_id=options.user_id,
        scope=options.scope,
    )


@router.delete(
    "/documents/{id}/permanent",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete document from storage and database",
)
async def permanent_delete_document(
    id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
    options: WriteOptions = Depends(get_write_options),
) -> None:
    """Permanently delete file from storage provider and purge database record."""
    await service.permanent_delete_document(id, options=options)


router.include_router(local_router)
