from datetime import UTC, datetime, timedelta

import anyio
from azure.storage.blob import (
    BlobSasPermissions,
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.storage.providers.base import PresignedUrlMethod

__all__ = ["AzureBlobStorageProvider"]


class AzureBlobStorageProvider:
    """Azure Blob Storage provider using azure-storage-blob."""

    def __init__(
        self,
        container: str | None = None,
        connection_string: str | None = None,
        account_name: str | None = None,
        account_key: str | None = None,
    ) -> None:
        self.container = (
            container or settings.storage_azure_container or settings.storage_bucket
        )
        conn_str = connection_string or settings.storage_azure_connection_string
        self.account_name = account_name or settings.storage_azure_account_name
        self.account_key = account_key or settings.storage_azure_account_key

        if conn_str:
            self.service_client = BlobServiceClient.from_connection_string(conn_str)
        elif self.account_name and self.account_key:
            account_url = f"https://{self.account_name}.blob.core.windows.net"
            self.service_client = BlobServiceClient(
                account_url=account_url,
                credential=self.account_key,
            )
        else:
            raise ValueError(
                "Azure Blob Storage requires connection string or credentials."
            )

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload blob to Azure container."""
        content_settings = (
            ContentSettings(content_type=content_type) if content_type else None
        )

        def _upload() -> None:
            blob_client = self.service_client.get_blob_client(
                container=self.container, blob=key
            )
            blob_client.upload_blob(
                data, overwrite=True, content_settings=content_settings
            )

        await anyio.to_thread.run_sync(_upload)

    async def download(self, key: str) -> bytes:
        """Download blob bytes from Azure container."""

        def _download() -> bytes:
            blob_client = self.service_client.get_blob_client(
                container=self.container, blob=key
            )
            return blob_client.download_blob().readall()  # type: ignore[no-any-return]

        return await anyio.to_thread.run_sync(_download)

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate Shared Access Signature (SAS) URL for Azure blob."""
        if not self.account_name or not self.account_key:
            raise ValueError(
                "account_name and account_key are required to generate Azure SAS URLs."
            )

        method_str = str(method).upper()
        permissions = (
            BlobSasPermissions(read=True)
            if method_str == "GET"
            else BlobSasPermissions(write=True, create=True)
        )
        expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

        def _sas() -> str:
            sas_token = generate_blob_sas(
                account_name=str(self.account_name),
                container_name=self.container,
                blob_name=key,
                account_key=str(self.account_key),
                permission=permissions,
                expiry=expiry,
            )
            blob_client = self.service_client.get_blob_client(
                container=self.container, blob=key
            )
            return f"{blob_client.url}?{sas_token}"

        return await anyio.to_thread.run_sync(_sas)

    async def delete(self, key: str) -> None:
        """Delete blob from Azure container."""

        def _delete() -> None:
            blob_client = self.service_client.get_blob_client(
                container=self.container, blob=key
            )
            blob_client.delete_blob()

        await anyio.to_thread.run_sync(_delete)

    async def exists(self, key: str) -> bool:
        """Check if blob exists in Azure container."""

        def _exists() -> bool:
            blob_client = self.service_client.get_blob_client(
                container=self.container, blob=key
            )
            return bool(blob_client.exists())

        return await anyio.to_thread.run_sync(_exists)
