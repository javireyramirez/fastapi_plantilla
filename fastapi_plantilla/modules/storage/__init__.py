import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.storage.dependencies import get_storage_provider
from fastapi_plantilla.modules.storage.models import Document
from fastapi_plantilla.modules.storage.providers import (
    AzureBlobStorageProvider,
    GoogleCloudStorageProvider,
    LocalStorageProvider,
    MockStorageProvider,
    PresignedUrlMethod,
    S3StorageProvider,
    StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import DocumentRepository
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.storage.service import DocumentService
from fastapi_plantilla.modules.trash.service import register_trash_entity

__all__ = [
    "AzureBlobStorageProvider",
    "Document",
    "DocumentRepository",
    "DocumentService",
    "GoogleCloudStorageProvider",
    "LocalStorageProvider",
    "MockStorageProvider",
    "PresignedUrlMethod",
    "S3StorageProvider",
    "StorageProvider",
    "storage_router",
]


async def _purge_document_storage(session: AsyncSession, entity_id: uuid.UUID) -> None:
    """Delete physical file from storage provider when document is purged from trash."""
    doc = await session.get(Document, entity_id)
    if doc is not None and doc.file_key:
        provider = get_storage_provider()
        try:
            await provider.delete(doc.file_key)
        except Exception as err:
            logger.warning(
                f"Failed to delete storage file {doc.file_key} on purge: {err}"
            )


register_trash_entity("document", Document, _purge_document_storage)
register_trash_entity("documents", Document, _purge_document_storage)
