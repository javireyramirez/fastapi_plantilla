import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.storage.dependencies import get_storage_provider
from fastapi_plantilla.modules.storage.exceptions import StorageBucketNotFoundError
from fastapi_plantilla.modules.storage.jobs import (
    StorageCompressJobPayload,
    handle_storage_compress,
)
from fastapi_plantilla.modules.storage.models import Storage
from fastapi_plantilla.modules.storage.providers import (
    AzureBlobStorageProvider,
    GoogleCloudStorageProvider,
    LocalStorageProvider,
    MockStorageProvider,
    PresignedUrlMethod,
    S3StorageProvider,
    StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import StorageRepository
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.storage.service import StorageService
from fastapi_plantilla.modules.trash.service import register_trash_entity

__all__ = [
    "AzureBlobStorageProvider",
    "GoogleCloudStorageProvider",
    "LocalStorageProvider",
    "MockStorageProvider",
    "PresignedUrlMethod",
    "S3StorageProvider",
    "Storage",
    "StorageBucketNotFoundError",
    "StorageCompressJobPayload",
    "StorageProvider",
    "StorageRepository",
    "StorageService",
    "handle_storage_compress",
    "storage_router",
]


async def _purge_storage_file(session: AsyncSession, entity_id: uuid.UUID) -> None:
    """Delete physical file from storage provider when item is purged from trash."""
    record = await session.get(Storage, entity_id)
    if record is not None and record.file_key:
        provider = get_storage_provider()
        try:
            await provider.delete(record.file_key)
        except Exception as err:
            logger.warning(
                f"Failed to delete storage file {record.file_key} on purge: {err}"
            )


register_trash_entity("storage", Storage, _purge_storage_file)
