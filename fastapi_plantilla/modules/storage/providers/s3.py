from typing import Any

import anyio
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from loguru import logger

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.storage.providers.base import (
    PresignedUrlMethod,
    StorageMetadata,
)

__all__ = ["S3StorageProvider"]


class S3StorageProvider:
    """AWS S3 and MinIO storage provider using boto3."""

    def __init__(
        self,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        public_endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ) -> None:
        self.bucket = bucket or settings.storage_bucket
        resolved_endpoint = endpoint_url or settings.storage_s3_endpoint_url
        resolved_public_endpoint = (
            public_endpoint_url
            or settings.storage_s3_public_endpoint_url
            or resolved_endpoint
        )
        resolved_access_key = access_key or settings.storage_s3_access_key
        resolved_secret_key = secret_key or settings.storage_s3_secret_key
        resolved_region = region or settings.storage_s3_region

        self.client = boto3.client(
            "s3",
            endpoint_url=resolved_endpoint,
            aws_access_key_id=resolved_access_key,
            aws_secret_access_key=resolved_secret_key,
            region_name=resolved_region,
            config=Config(signature_version="s3v4"),
        )
        if resolved_public_endpoint != resolved_endpoint:
            self.public_client = boto3.client(
                "s3",
                endpoint_url=resolved_public_endpoint,
                aws_access_key_id=resolved_access_key,
                aws_secret_access_key=resolved_secret_key,
                region_name=resolved_region,
                config=Config(signature_version="s3v4"),
            )
        else:
            self.public_client = self.client

    async def upload(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        """Upload object to S3 bucket."""
        extra_args: dict[str, Any] = {}
        if content_type:
            extra_args["ContentType"] = content_type

        def _put() -> None:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=data,
                **extra_args,
            )

        await anyio.to_thread.run_sync(_put)

    async def download(self, key: str) -> bytes:
        """Download object from S3 bucket."""

        def _get() -> bytes:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()  # type: ignore[no-any-return]

        return await anyio.to_thread.run_sync(_get)

    async def get_presigned_url(
        self,
        key: str,
        expires_in: int = 3600,
        method: PresignedUrlMethod | str = PresignedUrlMethod.GET,
    ) -> str:
        """Generate presigned URL for S3 object."""
        method_str = str(method).upper()
        client_method = "get_object" if method_str == "GET" else "put_object"
        params = {"Bucket": self.bucket, "Key": key}

        def _generate() -> str:
            return self.public_client.generate_presigned_url(  # type: ignore[no-any-return]
                ClientMethod=client_method,
                Params=params,
                ExpiresIn=expires_in,
            )

        return await anyio.to_thread.run_sync(_generate)

    async def delete(self, key: str) -> None:
        """Delete object from S3 bucket."""
        await anyio.to_thread.run_sync(
            lambda: self.client.delete_object(Bucket=self.bucket, Key=key)
        )

    async def exists(self, key: str) -> bool:
        """Check if object exists in S3 bucket."""

        def _head() -> bool:
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
                return True
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return False
                logger.error(f"S3 head_object error for key '{key}': {e}")
                raise

        return await anyio.to_thread.run_sync(_head)

    async def get_metadata(self, key: str) -> StorageMetadata | None:
        """Retrieve object metadata (size, content type) from S3."""

        def _head() -> StorageMetadata | None:
            try:
                resp = self.client.head_object(Bucket=self.bucket, Key=key)
                return StorageMetadata(
                    size_bytes=int(resp.get("ContentLength", 0)),
                    content_type=resp.get("ContentType"),
                )
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code")
                if code in ("404", "NoSuchKey", "NotFound"):
                    return None
                logger.error(f"S3 head_object error for key '{key}': {e}")
                raise

        return await anyio.to_thread.run_sync(_head)
