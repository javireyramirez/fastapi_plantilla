from typing import Any

import anyio
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from loguru import logger

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.storage.exceptions import StorageBucketNotFoundError
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
        self._bucket_verified: bool = False

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

    async def check_bucket_exists(self) -> bool:
        """Check whether the configured S3/MinIO bucket exists and is accessible."""

        def _head() -> bool:
            try:
                self.client.head_bucket(Bucket=self.bucket)
                return True
            except ClientError as e:
                code = str(e.response.get("Error", {}).get("Code", ""))
                if code in ("404", "NoSuchBucket", "NotFound"):
                    return False
                logger.warning(
                    f"S3 head_bucket returned error for '{self.bucket}': {e}"
                )
                return False
            except Exception as e:
                logger.warning(f"Failed checking bucket '{self.bucket}': {e}")
                return False

        return await anyio.to_thread.run_sync(_head)

    async def ensure_bucket_exists(self) -> bool:
        """Verify bucket existence.

        If environment is development/local/test, auto-creates bucket and
        applies permissive CORS rules. In production environments, only
        verifies existence without attempting auto-creation.
        """
        if self._bucket_verified:
            return True

        exists = await self.check_bucket_exists()
        if exists:
            self._bucket_verified = True
            return True

        if not settings.is_dev:
            logger.error(
                f"Production storage bucket '{self.bucket}' does NOT exist. "
                "Auto-creation is disabled in production environments."
            )
            return False

        logger.info(
            f"Dev environment detected: auto-creating bucket '{self.bucket}'..."
        )

        def _create_and_cors() -> None:
            try:
                kwargs: dict[str, Any] = {"Bucket": self.bucket}
                region = self.client.meta.region_name
                if region and region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
                self.client.create_bucket(**kwargs)
                logger.info(f"Dev bucket '{self.bucket}' created successfully.")
            except ClientError as e:
                code = str(e.response.get("Error", {}).get("Code", ""))
                if code not in (
                    "BucketAlreadyOwnedByYou",
                    "BucketAlreadyExists",
                ):
                    logger.error(f"Failed creating dev bucket '{self.bucket}': {e}")
                    raise

            try:
                cors_cfg = {
                    "CORSRules": [
                        {
                            "AllowedHeaders": ["*"],
                            "AllowedMethods": [
                                "GET",
                                "PUT",
                                "POST",
                                "DELETE",
                                "HEAD",
                            ],
                            "AllowedOrigins": ["*"],
                            "ExposeHeaders": ["ETag", "x-amz-request-id"],
                            "MaxAgeSeconds": 3600,
                        }
                    ]
                }
                self.client.put_bucket_cors(
                    Bucket=self.bucket, CORSConfiguration=cors_cfg
                )
                logger.info(f"CORS policy applied to dev bucket '{self.bucket}'.")
            except Exception as e:
                logger.warning(f"Could not apply CORS to bucket '{self.bucket}': {e}")

        try:
            await anyio.to_thread.run_sync(_create_and_cors)
            self._bucket_verified = True
            return True
        except Exception as e:
            logger.error(f"Failed ensuring dev bucket '{self.bucket}': {e}")
            return False

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
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    **extra_args,
                )
            except ClientError as e:
                code = str(e.response.get("Error", {}).get("Code", ""))
                if code in ("NoSuchBucket", "404", "NotFound"):
                    raise StorageBucketNotFoundError(self.bucket) from e
                raise

        await anyio.to_thread.run_sync(_put)

    async def download(self, key: str) -> bytes:
        """Download object from S3 bucket."""

        def _get() -> bytes:
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
                return response["Body"].read()  # type: ignore[no-any-return]
            except ClientError as e:
                code = str(e.response.get("Error", {}).get("Code", ""))
                if code in ("NoSuchBucket", "404", "NotFound"):
                    raise StorageBucketNotFoundError(self.bucket) from e
                raise

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
