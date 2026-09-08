import hashlib
import hmac
import time
from pathlib import Path

import anyio

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.storage.providers.base import PresignedUrlMethod

__all__ = ["LocalStorageProvider"]


class LocalStorageProvider:
    """Local filesystem storage provider with HMAC-SHA256 signed URLs."""

    def __init__(
        self,
        base_path: Path | None = None,
        secret: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.base_path = (base_path or settings.storage_local_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.secret = secret or settings.auth_secret
        self.base_url = (
            base_url
            or settings.storage_local_base_url
            or f"http://{settings.host}:{settings.port}"
        ).rstrip("/")

    def _resolve_path(self, key: str) -> Path:
        """Resolve path and guard against directory traversal."""
        clean_key = key.lstrip("/\\")
        target_path = (self.base_path / clean_key).resolve()
        if not target_path.is_relative_to(self.base_path):
            raise ValueError(f"Directory traversal detected in storage key: {key}")
        return target_path

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Save file bytes to local filesystem."""
        path = self._resolve_path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await anyio.to_thread.run_sync(_write)

    async def download(self, key: str) -> bytes:
        """Read file bytes from local filesystem."""
        path = self._resolve_path(key)

        def _read() -> bytes:
            if not path.is_file():
                raise FileNotFoundError(f"Storage file not found: {key}")
            return path.read_bytes()

        return await anyio.to_thread.run_sync(_read)

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate local signed URL with HMAC-SHA256 signature."""
        method_str = str(method).upper()
        expires_ts = int(time.time()) + expires_in
        sig = self.generate_signature(key, expires_ts, method_str)
        clean_key = key.lstrip("/")
        return (
            f"{self.base_url}/api/storage/local-files/{clean_key}?"
            f"expires={expires_ts}&signature={sig}&method={method_str}"
        )

    def generate_signature(self, key: str, expires: int, method: str) -> str:
        """Generate cryptographic HMAC signature for local signed URL."""
        msg = f"{method.upper()}:{key.lstrip('/')}:{expires}".encode()
        return hmac.new(self.secret.encode(), msg, hashlib.sha256).hexdigest()

    def verify_signature(
        self, key: str, expires: int, signature: str, method: str
    ) -> bool:
        """Verify signature in constant time and check expiry."""
        if int(time.time()) > expires:
            return False
        expected = self.generate_signature(key, expires, method)
        return hmac.compare_digest(expected, signature)

    async def delete(self, key: str) -> None:
        """Delete local file if it exists."""
        path = self._resolve_path(key)

        def _delete() -> None:
            if path.is_file():
                path.unlink()

        await anyio.to_thread.run_sync(_delete)

    async def exists(self, key: str) -> bool:
        """Check if local file exists."""
        path = self._resolve_path(key)
        return await anyio.to_thread.run_sync(path.is_file)
