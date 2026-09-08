import enum
from typing import Protocol, runtime_checkable

__all__ = ["PresignedUrlMethod", "StorageProvider"]


class PresignedUrlMethod(enum.StrEnum):
    """Supported HTTP methods for presigned URLs."""

    GET = "GET"
    PUT = "PUT"


@runtime_checkable
class StorageProvider(Protocol):
    """Agnostic protocol for cloud and local storage providers."""

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload raw bytes to storage with the specified key."""
        ...

    async def download(self, key: str) -> bytes:
        """Download raw bytes from storage matching the key."""
        ...

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate a presigned URL for direct upload (PUT) or download (GET)."""
        ...

    async def delete(self, key: str) -> None:
        """Delete an object from storage."""
        ...

    async def exists(self, key: str) -> bool:
        """Check whether an object exists in storage."""
        ...
