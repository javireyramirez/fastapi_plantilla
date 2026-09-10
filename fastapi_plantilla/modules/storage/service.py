import io
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from loguru import logger

from fastapi_plantilla.core.crud.schema import (
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_owned import BaseOwnedService
from fastapi_plantilla.core.mixins import RecordStatus, generate_uuid7
from fastapi_plantilla.modules.storage.models import Document
from fastapi_plantilla.modules.storage.providers import (
    PresignedUrlMethod,
    StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import DocumentRepository
from fastapi_plantilla.modules.storage.schema import (
    ConfirmUploadRequest,
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
    ZipDownloadRequest,
)

DEFAULT_PRESIGNED_EXPIRY_SECONDS: int = 3600
DEFAULT_ZIP_FILENAME: str = "documents.zip"

__all__ = [
    "DEFAULT_PRESIGNED_EXPIRY_SECONDS",
    "DEFAULT_ZIP_FILENAME",
    "DocumentService",
    "sanitize_filename",
]


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe characters."""
    clean = Path(filename).name
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", clean)


class DocumentService(BaseOwnedService[Document]):
    """Domain service managing document lifecycle, storage and RBAC."""

    resource_name: str = "Document"
    display_field: str = "name"
    mask_forbidden_as_not_found: bool = True
    owner_field: str = "owner_id"

    def __init__(
        self,
        repository: DocumentRepository,
        storage_provider: StorageProvider,
    ) -> None:
        super().__init__(repository)
        self.doc_repo = repository
        self.storage_provider = storage_provider

    def build_storage_key(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        doc_id: uuid.UUID,
        filename: str,
    ) -> str:
        """Construct canonical, safe storage key path."""
        safe_name = sanitize_filename(filename)
        return f"documents/{entity_type}/{entity_id}/{doc_id}_{safe_name}"

    async def request_presigned_upload(
        self,
        data: PresignedUploadRequest,
        options: WriteOptions | None = None,
    ) -> PresignedUploadResponse:
        """Generate presigned upload URL and register pending document."""
        doc_id = generate_uuid7()
        safe_name = sanitize_filename(data.name)
        extension = Path(safe_name).suffix.lstrip(".").lower() or None
        content_type = (
            data.content_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        file_key = self.build_storage_key(
            data.entity_type, data.entity_id, doc_id, safe_name
        )

        upload_url = await self.storage_provider.get_presigned_url(
            key=file_key,
            expires_in=DEFAULT_PRESIGNED_EXPIRY_SECONDS,
            method=PresignedUrlMethod.PUT,
        )

        create_payload: dict[str, Any] = {
            "id": doc_id,
            "entity_type": data.entity_type,
            "entity_id": data.entity_id,
            "name": safe_name,
            "file_key": file_key,
            "content_type": content_type,
            "size_bytes": data.size_bytes or 0,
            "extension": extension,
            "description": data.description,
            "is_uploaded": False,
        }

        user_id = options.user_id if options else None
        scope = options.scope if options else None
        await self.create(
            create_payload,
            user_id=user_id,
            scope=scope,
            allow_immutable=True,
        )

        return PresignedUploadResponse(
            document_id=doc_id,
            upload_url=upload_url,
            file_key=file_key,
            expires_in=DEFAULT_PRESIGNED_EXPIRY_SECONDS,
            method="PUT",
        )

    async def confirm_upload(
        self,
        id: uuid.UUID,
        data: ConfirmUploadRequest | None = None,
        options: WriteOptions | None = None,
    ) -> Document:
        """Confirm that file was uploaded to storage provider."""
        scope = options.scope if options else None
        user_id = options.user_id if options else None
        document = await self.get_by_id(id, scope=scope)

        exists = await self.storage_provider.exists(document.file_key)
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File has not been uploaded to storage yet.",
            )

        update_payload: dict[str, Any] = {"is_uploaded": True}
        if data:
            if data.size_bytes is not None:
                update_payload["size_bytes"] = data.size_bytes
            if data.content_type is not None:
                update_payload["content_type"] = data.content_type

        return await self.update(
            id,
            update_payload,
            user_id=user_id,
            scope=scope,
        )

    async def upload_direct(
        self,
        file_data: bytes,
        filename: str,
        entity_type: str,
        entity_id: uuid.UUID,
        content_type: str | None = None,
        description: str | None = None,
        options: WriteOptions | None = None,
    ) -> Document:
        """Direct file upload bypassing client-side presigned URLs."""
        doc_id = generate_uuid7()
        safe_name = sanitize_filename(filename)
        extension = Path(safe_name).suffix.lstrip(".").lower() or None
        mime = (
            content_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        file_key = self.build_storage_key(entity_type, entity_id, doc_id, safe_name)

        await self.storage_provider.upload(
            key=file_key,
            data=file_data,
            content_type=mime,
        )

        create_payload: dict[str, Any] = {
            "id": doc_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "name": safe_name,
            "file_key": file_key,
            "content_type": mime,
            "size_bytes": len(file_data),
            "extension": extension,
            "description": description,
            "is_uploaded": True,
        }

        user_id = options.user_id if options else None
        scope = options.scope if options else None
        return await self.create(
            create_payload,
            user_id=user_id,
            scope=scope,
            allow_immutable=True,
        )

    async def get_presigned_download(
        self,
        id: uuid.UUID,
        expires_in: int = DEFAULT_PRESIGNED_EXPIRY_SECONDS,
        scope: ScopeContext | None = None,
    ) -> PresignedDownloadResponse:
        """Generate presigned download URL for an active document."""
        document = await self.get_by_id(id, scope=scope)

        if not document.is_uploaded:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document file has not been uploaded yet.",
            )

        download_url = await self.storage_provider.get_presigned_url(
            key=document.file_key,
            expires_in=expires_in,
            method=PresignedUrlMethod.GET,
        )

        return PresignedDownloadResponse(
            document_id=document.id,
            download_url=download_url,
            expires_in=expires_in,
            name=document.name,
            content_type=document.content_type,
        )

    async def download_content(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> tuple[bytes, str, str]:
        """Download document content bytes directly from storage."""
        document = await self.get_by_id(id, scope=scope)

        if not document.is_uploaded:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document file has not been uploaded yet.",
            )

        data = await self.storage_provider.download(document.file_key)
        return data, document.name, document.content_type

    def _filter_accessible_docs(
        self,
        docs: list[Document],
        scope: ScopeContext | None,
    ) -> list[Document]:
        """Filter documents matching upload state and RBAC scope."""
        accessible: list[Document] = []
        is_own_scope = bool(
            scope
            and not scope.is_super_admin
            and str(scope.scope).upper() == ScopeType.OWN
        )
        for doc in docs:
            if not doc.is_uploaded or doc.status == RecordStatus.TRASHED:
                continue
            if is_own_scope and scope and doc.owner_id != scope.user_id:
                continue
            accessible.append(doc)
        return accessible

    async def download_zip(
        self,
        request: ZipDownloadRequest,
        scope: ScopeContext | None = None,
    ) -> tuple[bytes, str]:
        """Download multiple documents bundled in a ZIP archive."""
        if request.document_ids:
            candidates = await self.doc_repo.find_uploaded_by_ids(request.document_ids)
        elif request.entity_type and request.entity_id:
            candidates = await self.doc_repo.find_by_entity(
                request.entity_type, request.entity_id
            )
        else:
            candidates = []

        accessible_docs = self._filter_accessible_docs(candidates, scope)

        if not accessible_docs:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No accessible uploaded documents found for criteria.",
            )

        zip_buffer = io.BytesIO()
        seen_names: dict[str, int] = {}

        with zipfile.ZipFile(
            zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for doc in accessible_docs:
                try:
                    file_bytes = await self.storage_provider.download(doc.file_key)
                except Exception as err:
                    logger.warning(f"Failed downloading {doc.file_key} for zip: {err}")
                    continue

                count = seen_names.get(doc.name, 0) + 1
                seen_names[doc.name] = count
                filename = (
                    doc.name
                    if count == 1
                    else f"{Path(doc.name).stem}_{count}{Path(doc.name).suffix}"
                )
                zf.writestr(filename, file_bytes)

        return zip_buffer.getvalue(), DEFAULT_ZIP_FILENAME

    async def permanent_delete_document(
        self,
        id: uuid.UUID,
        options: WriteOptions | None = None,
    ) -> None:
        """Permanently delete file from storage and database."""
        scope = options.scope if options else None
        user_id = options.user_id if options else None
        document = await self.get_by_id(id, scope=scope)

        try:
            await self.storage_provider.delete(document.file_key)
        except Exception as err:
            logger.warning(f"Failed to delete storage file {document.file_key}: {err}")

        if document.status != RecordStatus.TRASHED:
            await self.trash(id, user_id=user_id, scope=scope, options=options)
        await self.permanent_delete(id, scope=scope, options=options)
