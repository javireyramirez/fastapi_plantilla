from fastapi_plantilla.modules.storage.providers.base import PresignedUrlMethod

__all__ = ["MockStorageProvider"]


class MockStorageProvider:
    """In-memory storage provider for fast unit tests without filesystem I/O."""

    def __init__(self) -> None:
        self._storage: dict[str, bytes] = {}

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Store bytes in memory."""
        self._storage[key] = data

    async def download(self, key: str) -> bytes:
        """Retrieve bytes from memory."""
        if key not in self._storage:
            raise FileNotFoundError(f"Mock file not found: {key}")
        return self._storage[key]

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate mock presigned URL."""
        return f"mock://storage/{key}?method={method}&expires={expires_in}"

    async def delete(self, key: str) -> None:
        """Remove file from in-memory storage."""
        self._storage.pop(key, None)

    async def exists(self, key: str) -> bool:
        """Check if file exists in memory."""
        return key in self._storage
