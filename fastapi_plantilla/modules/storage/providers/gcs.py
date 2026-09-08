from datetime import timedelta

import anyio
from google.cloud import storage as gcs

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.storage.providers.base import PresignedUrlMethod

__all__ = ["GoogleCloudStorageProvider"]


class GoogleCloudStorageProvider:
    """Google Cloud Storage provider using google-cloud-storage."""

    def __init__(
        self,
        bucket_name: str | None = None,
        credentials_file: str | None = None,
        project: str | None = None,
    ) -> None:
        self.bucket_name = bucket_name or settings.storage_bucket
        cred_file = credentials_file or settings.storage_gcs_credentials_file
        proj = project or settings.storage_gcs_project

        if cred_file:
            self.client = gcs.Client.from_service_account_json(cred_file, project=proj)
        else:
            self.client = gcs.Client(project=proj)
        self.bucket = self.client.bucket(self.bucket_name)

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload blob to GCS bucket."""

        def _upload() -> None:
            blob = self.bucket.blob(key)
            blob.upload_from_string(data, content_type=content_type)

        await anyio.to_thread.run_sync(_upload)

    async def download(self, key: str) -> bytes:
        """Download blob bytes from GCS bucket."""

        def _download() -> bytes:
            blob = self.bucket.blob(key)
            return blob.download_as_bytes()

        return await anyio.to_thread.run_sync(_download)

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate signed URL for GCS blob."""
        method_str = str(method).upper()
        expiration = timedelta(seconds=expires_in)

        def _signed_url() -> str:
            blob = self.bucket.blob(key)
            return blob.generate_signed_url(  # type: ignore[no-any-return]
                version="v4",
                expiration=expiration,
                method=method_str,
            )

        return await anyio.to_thread.run_sync(_signed_url)

    async def delete(self, key: str) -> None:
        """Delete blob from GCS bucket."""

        def _delete() -> None:
            blob = self.bucket.blob(key)
            blob.delete()

        await anyio.to_thread.run_sync(_delete)

    async def exists(self, key: str) -> bool:
        """Check if blob exists in GCS bucket."""

        def _exists() -> bool:
            blob = self.bucket.blob(key)
            return bool(blob.exists())

        return await anyio.to_thread.run_sync(_exists)
