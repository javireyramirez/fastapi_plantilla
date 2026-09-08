from functools import lru_cache

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import StorageBackend, settings
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.storage.providers import (
    AzureBlobStorageProvider,
    GoogleCloudStorageProvider,
    LocalStorageProvider,
    S3StorageProvider,
    StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import DocumentRepository
from fastapi_plantilla.modules.storage.service import DocumentService

__all__ = [
    "get_document_repository",
    "get_document_service",
    "get_storage_provider",
    "set_storage_provider_override",
]

_OVERRIDE_STORE: dict[str, StorageProvider | None] = {"provider": None}


def set_storage_provider_override(provider: StorageProvider | None) -> None:
    """Configure or clear singleton storage provider override."""
    _OVERRIDE_STORE["provider"] = provider


def get_storage_provider() -> StorageProvider:
    """Provide storage provider strategy based on settings or override."""
    override = _OVERRIDE_STORE.get("provider")
    if override is not None:
        return override
    return _get_cached_storage_provider()


@lru_cache
def _get_cached_storage_provider() -> StorageProvider:
    """Provide cached singleton storage provider strategy based on settings."""
    backend = settings.storage_backend
    if backend == StorageBackend.LOCAL:
        return LocalStorageProvider()
    if backend == StorageBackend.S3:
        return S3StorageProvider()
    if backend == StorageBackend.AZURE:
        return AzureBlobStorageProvider()
    if backend == StorageBackend.GCS:
        return GoogleCloudStorageProvider()
    raise ValueError(f"Unsupported storage backend: {backend}")


def get_document_repository(
    session: AsyncSession = Depends(get_db_session),
) -> DocumentRepository:
    """Provide DocumentRepository instance bound to request DB session."""
    return DocumentRepository(session=session)


def get_document_service(
    repository: DocumentRepository = Depends(get_document_repository),
    storage_provider: StorageProvider = Depends(get_storage_provider),
) -> DocumentService:
    """Provide DocumentService instance."""
    return DocumentService(repository=repository, storage_provider=storage_provider)
