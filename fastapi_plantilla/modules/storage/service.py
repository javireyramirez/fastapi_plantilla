import io
import mimetypes
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import inspect, select

from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    PaginationParams,
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_owned import BaseOwnedService
from fastapi_plantilla.core.mixins import RecordStatus, generate_uuid7
from fastapi_plantilla.modules.common import (
    enrich_principal_entities,
    resolve_entity_model,
)
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.storage.models import Storage
from fastapi_plantilla.modules.storage.providers import (
    PresignedUrlMethod,
    StorageProvider,
)
from fastapi_plantilla.modules.storage.repository import StorageRepository
from fastapi_plantilla.modules.storage.schema import (
    ConfirmUploadRequest,
    CreateExternalUrlRequest,
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
    ZipDownloadRequest,
)

DEFAULT_PRESIGNED_EXPIRY_SECONDS: int = 3600
DEFAULT_ZIP_FILENAME: str = "storage.zip"
DEFAULT_MAX_UPLOAD_SIZE_BYTES: int = 52428800  # 50 MB
DEFAULT_MAX_ZIP_TOTAL_BYTES: int = 104857600  # 100 MB
DEFAULT_MAX_ZIP_FILE_COUNT: int = 100
DEFAULT_ORPHAN_RETENTION_SECONDS: int = 86400  # 24 hours

__all__ = [
    "DEFAULT_MAX_UPLOAD_SIZE_BYTES",
    "DEFAULT_MAX_ZIP_FILE_COUNT",
    "DEFAULT_MAX_ZIP_TOTAL_BYTES",
    "DEFAULT_ORPHAN_RETENTION_SECONDS",
    "DEFAULT_PRESIGNED_EXPIRY_SECONDS",
    "DEFAULT_ZIP_FILENAME",
    "StorageService",
    "sanitize_filename",
]


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent path traversal and unsafe characters."""
    clean = Path(filename).name
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", clean)


class StorageService(BaseOwnedService[Storage]):
    """Domain service managing storage lifecycle, storage providers and RBAC."""

    resource_name: str = "Storage"
    display_field: str = "name"
    mask_forbidden_as_not_found: bool = True
    owner_field: str = "owner_id"
    search_fields: ClassVar[list[str]] = ["name", "description", "file_key"]

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query clauses including storage entity criteria and content type."""
        clauses = super().build_where_filters(params)

        entity_id = getattr(params, "entity_id", None)
        if entity_id is not None:
            clauses.append(Storage.entity_id == entity_id)

        entity_type = getattr(params, "entity_type", None)
        if entity_type:
            clauses.append(Storage.entity_type == entity_type)

        is_uploaded = getattr(params, "is_uploaded", None)
        if is_uploaded is not None:
            clauses.append(Storage.is_uploaded == is_uploaded)

        content_types = getattr(params, "content_types", None)
        if content_types:
            clauses.append(Storage.content_type.in_(content_types))

        size_min = getattr(params, "size_min", None)
        if size_min is not None:
            clauses.append(Storage.size_bytes >= size_min)

        size_max = getattr(params, "size_max", None)
        if size_max is not None:
            clauses.append(Storage.size_bytes <= size_max)

        return clauses

    def __init__(
        self,
        repository: StorageRepository,
        storage_provider: StorageProvider,
        settings_service: SystemSettingService | None = None,
    ) -> None:
        super().__init__(repository)
        self.storage_repo = repository
        self.storage_provider = storage_provider
        self.settings_service = settings_service

    async def find_paginated(
        self,
        params: Any,
        *where: Any,
        scope: ScopeContext | None = None,
        order_by: Any = None,
    ) -> PaginatedResponse[Storage]:
        """Fetch paginated storage records enriched with principal entity metadata."""
        res = await super().find_paginated(
            params, *where, scope=scope, order_by=order_by
        )
        await enrich_principal_entities(self.repository.session, res.data)
        return res

    async def get_by_id(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> Storage:
        """Fetch storage record by ID enriched with principal entity metadata."""
        record = await super().get_by_id(id, *where, scope=scope)
        await enrich_principal_entities(self.repository.session, [record])
        return record

    def build_storage_key(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        storage_id: uuid.UUID,
        filename: str,
    ) -> str:
        """Construct canonical, safe storage key path."""
        safe_name = sanitize_filename(filename)
        return f"storage/{entity_type}/{entity_id}/{storage_id}_{safe_name}"

    async def get_max_upload_size(self) -> int:
        """Get configured maximum upload size limit in bytes."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "storage.max_upload_size_bytes",
                    default=DEFAULT_MAX_UPLOAD_SIZE_BYTES,
                )
            )
        return DEFAULT_MAX_UPLOAD_SIZE_BYTES

    async def get_max_zip_total_bytes(self) -> int:
        """Get configured maximum total bytes for ZIP archives."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "storage.max_zip_total_bytes",
                    default=DEFAULT_MAX_ZIP_TOTAL_BYTES,
                )
            )
        return DEFAULT_MAX_ZIP_TOTAL_BYTES

    async def get_max_zip_file_count(self) -> int:
        """Get configured maximum number of files per ZIP archive."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "storage.max_zip_file_count",
                    default=DEFAULT_MAX_ZIP_FILE_COUNT,
                )
            )
        return DEFAULT_MAX_ZIP_FILE_COUNT

    async def get_presigned_expiry_seconds(self) -> int:
        """Get configured default expiry seconds for presigned URLs."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "storage.presigned_expiry_seconds",
                    default=DEFAULT_PRESIGNED_EXPIRY_SECONDS,
                )
            )
        return DEFAULT_PRESIGNED_EXPIRY_SECONDS

    async def get_orphan_retention_seconds(self) -> int:
        """Get configured retention seconds for pending orphan uploads."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "storage.orphan_retention_seconds",
                    default=DEFAULT_ORPHAN_RETENTION_SECONDS,
                )
            )
        return DEFAULT_ORPHAN_RETENTION_SECONDS

    async def _validate_entity_access(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        scope: ScopeContext | None,
    ) -> None:
        """Verify caller authority (user/team) or referential integrity (known models).

        File authorization is governed by the storage record's owner_id/team_id and
        RBAC scope. Target entity checks ensure referential integrity.
        """
        if not scope or scope.is_super_admin:
            return

        scope_str = str(scope.scope).upper()
        if scope_str == ScopeType.GLOBAL:
            return

        norm_type = entity_type.strip().lower()
        if norm_type in ("user", "users"):
            self._check_user_entity_access(entity_id, scope, scope_str)
        elif norm_type in ("team", "teams"):
            self._check_team_entity_access(entity_id, scope)
        else:
            await self._ensure_target_entity_exists(norm_type, entity_id)

    async def _ensure_target_entity_exists(
        self,
        norm_type: str,
        entity_id: uuid.UUID,
    ) -> None:
        """Verify target known entity exists in database (referential integrity)."""
        model = resolve_entity_model(norm_type)
        if model is not None:
            try:
                pk_col = inspect(model).primary_key[0]
                stmt = select(pk_col).where(pk_col == entity_id)
                res = await self.repository.session.execute(stmt)
                if res.scalar_one_or_none() is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Target {norm_type} entity '{entity_id}' not found.",
                    )
            except HTTPException:
                raise
            except Exception as err:
                logger.error(
                    "Failed to check entity existence for %s %s: %s",
                    norm_type,
                    entity_id,
                    err,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to verify target entity referential integrity.",
                ) from err

    def _check_user_entity_access(
        self,
        entity_id: uuid.UUID,
        scope: ScopeContext,
        scope_str: str,
    ) -> None:
        """Enforce RBAC isolation when attaching files to users."""
        if scope_str == ScopeType.OWN and scope.user_id and entity_id != scope.user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot attach files to another user.",
            )
        if scope_str == ScopeType.TEAM:
            allowed = set(scope.teammate_ids)
            if scope.user_id:
                allowed.add(scope.user_id)
            if allowed and entity_id not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: cannot attach files outside your team.",
                )

    def _check_team_entity_access(
        self,
        entity_id: uuid.UUID,
        scope: ScopeContext,
    ) -> None:
        """Enforce RBAC isolation when attaching files to teams."""
        if scope.team_ids and entity_id not in scope.team_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: cannot attach files to an unassociated team.",
            )

    async def _validate_file_size(self, size_bytes: int | None) -> None:
        """Verify upload size does not exceed dynamic maximum limit."""
        if size_bytes is None:
            return
        max_size_bytes = await self.get_max_upload_size()
        if size_bytes > max_size_bytes:
            max_mb = max_size_bytes / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File size exceeds maximum limit of {max_mb:.1f} MB",
            )

    async def _validate_file_type(
        self,
        extension: str | None,
        content_type: str | None,
    ) -> None:
        """Verify extension and MIME type against allowed system settings."""
        if not self.settings_service:
            return

        categories = await self.settings_service.get_value(
            "storage.file_categories", default=None
        )

        allowed_exts = await self.settings_service.get_value(
            "storage.allowed_extensions", default=None
        )
        if not allowed_exts and categories:
            allowed_exts = [
                ext for cat in categories for ext in cat.get("extensions", [])
            ]

        if allowed_exts and extension:
            clean = [ext.lower().lstrip(".") for ext in allowed_exts]
            if extension.lower() not in clean:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File extension '.{extension}' is not permitted",
                )

        allowed_mimes = await self.settings_service.get_value(
            "storage.allowed_mimetypes", default=None
        )
        if not allowed_mimes and categories:
            allowed_mimes = [m for cat in categories for m in cat.get("mimes", [])]

        if allowed_mimes and content_type:
            is_allowed = any(
                content_type.startswith(p[:-2] + "/")
                if p.endswith("/*")
                else p.lower() == content_type.lower()
                for p in allowed_mimes
            )
            if not is_allowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"MIME type '{content_type}' is not permitted",
                )

    async def _validate_upload_limits(
        self,
        size_bytes: int | None,
        extension: str | None,
        content_type: str | None,
    ) -> None:
        """Validate upload against configured system settings."""
        await self._validate_file_size(size_bytes)
        await self._validate_file_type(extension, content_type)

    async def request_presigned_upload(
        self,
        data: PresignedUploadRequest,
        options: WriteOptions | None = None,
    ) -> PresignedUploadResponse:
        """Generate presigned upload URL and register pending storage record."""
        storage_id = generate_uuid7()
        safe_name = sanitize_filename(data.name)
        extension = Path(safe_name).suffix.lstrip(".").lower() or None
        content_type = (
            data.content_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        await self._validate_upload_limits(data.size_bytes, extension, content_type)

        file_key = self.build_storage_key(
            data.entity_type, data.entity_id, storage_id, safe_name
        )

        expires_in = await self.get_presigned_expiry_seconds()
        upload_url = await self.storage_provider.get_presigned_url(
            key=file_key,
            expires_in=expires_in,
            method=PresignedUrlMethod.PUT,
        )

        create_payload: dict[str, Any] = {
            "id": storage_id,
            "entity_type": data.entity_type,
            "entity_id": data.entity_id,
            "name": safe_name,
            "file_key": file_key,
            "content_type": content_type,
            "size_bytes": data.size_bytes or 0,
            "extension": extension,
            "description": data.description,
            "is_uploaded": False,
        }

        user_id = options.user_id if options else None
        scope = options.scope if options else None
        await self._validate_entity_access(data.entity_type, data.entity_id, scope)
        await self.create(
            create_payload,
            user_id=user_id,
            scope=scope,
            allow_immutable=True,
        )

        return PresignedUploadResponse(
            storage_id=storage_id,
            upload_url=upload_url,
            file_key=file_key,
            expires_in=expires_in,
            method="PUT",
        )

    async def confirm_upload(
        self,
        id: uuid.UUID,
        data: ConfirmUploadRequest | None = None,
        options: WriteOptions | None = None,
    ) -> Storage:
        """Confirm that file was uploaded using factual backend metadata."""
        scope = options.scope if options else None
        user_id = options.user_id if options else None
        storage_record = await self.get_by_id(id, scope=scope)

        meta = await self.storage_provider.get_metadata(storage_record.file_key)
        if meta is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File has not been uploaded to storage yet.",
            )

        mime = meta.content_type or storage_record.content_type
        if mime is None and data and data.content_type:
            mime = data.content_type
        await self._validate_upload_limits(
            meta.size_bytes, storage_record.extension, mime
        )

        update_payload: dict[str, Any] = {
            "is_uploaded": True,
            "size_bytes": meta.size_bytes,
        }
        if mime:
            update_payload["content_type"] = mime

        doc = await self.update(
            id,
            update_payload,
            user_id=user_id,
            scope=scope,
        )
        await enrich_principal_entities(self.repository.session, [doc])
        return doc

    async def upload_direct(
        self,
        file_data: bytes,
        filename: str,
        entity_type: str,
        entity_id: uuid.UUID,
        content_type: str | None = None,
        description: str | None = None,
        options: WriteOptions | None = None,
    ) -> Storage:
        """Direct file upload bypassing client-side presigned URLs."""
        user_id = options.user_id if options else None
        scope = options.scope if options else None
        await self._validate_entity_access(entity_type, entity_id, scope)

        storage_id = generate_uuid7()
        safe_name = sanitize_filename(filename)
        extension = Path(safe_name).suffix.lstrip(".").lower() or None
        mime = (
            content_type
            or mimetypes.guess_type(safe_name)[0]
            or "application/octet-stream"
        )
        await self._validate_upload_limits(len(file_data), extension, mime)
        file_key = self.build_storage_key(entity_type, entity_id, storage_id, safe_name)

        await self.storage_provider.upload(
            key=file_key,
            data=file_data,
            content_type=mime,
        )

        create_payload: dict[str, Any] = {
            "id": storage_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "name": safe_name,
            "file_key": file_key,
            "content_type": mime,
            "size_bytes": len(file_data),
            "extension": extension,
            "description": description,
            "is_uploaded": True,
        }

        record = await self.create(
            create_payload,
            user_id=user_id,
            scope=scope,
            allow_immutable=True,
        )
        await enrich_principal_entities(self.repository.session, [record])
        return record

    async def create_external_url(
        self,
        data: CreateExternalUrlRequest,
        options: WriteOptions | None = None,
    ) -> Storage:
        """Register an external URL resource directly as an active storage record."""
        user_id = options.user_id if options else None
        scope = options.scope if options else None
        await self._validate_entity_access(data.entity_type, data.entity_id, scope)

        storage_id = generate_uuid7()
        name = data.name.strip()
        url_str = str(data.url)
        url_path = url_str.split("?", 1)[0]
        extension = Path(url_path).suffix.lstrip(".").lower() or None
        content_type = mimetypes.guess_type(url_path)[0] or "application/x-external-url"
        file_key = f"external/{storage_id}"

        create_payload: dict[str, Any] = {
            "id": storage_id,
            "entity_type": data.entity_type,
            "entity_id": data.entity_id,
            "name": name,
            "file_key": file_key,
            "external_url": url_str,
            "content_type": content_type,
            "size_bytes": 0,
            "extension": extension,
            "description": data.description,
            "is_uploaded": True,
        }

        record = await self.create(
            create_payload,
            user_id=user_id,
            scope=scope,
            allow_immutable=True,
        )
        await enrich_principal_entities(self.repository.session, [record])

        return record

    async def get_presigned_download(
        self,
        id: uuid.UUID,
        expires_in: int = DEFAULT_PRESIGNED_EXPIRY_SECONDS,
        scope: ScopeContext | None = None,
    ) -> PresignedDownloadResponse:
        """Generate presigned download URL for an active storage file."""
        storage_record = await self.get_by_id(id, scope=scope)

        if not storage_record.is_uploaded:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File has not been uploaded yet.",
            )

        if storage_record.external_url:
            return PresignedDownloadResponse(
                storage_id=storage_record.id,
                download_url=storage_record.external_url,
                expires_in=expires_in,
                name=storage_record.name,
                content_type=storage_record.content_type,
            )

        download_url = await self.storage_provider.get_presigned_url(
            key=storage_record.file_key,
            expires_in=expires_in,
            method=PresignedUrlMethod.GET,
        )

        return PresignedDownloadResponse(
            storage_id=storage_record.id,
            download_url=download_url,
            expires_in=expires_in,
            name=storage_record.name,
            content_type=storage_record.content_type,
        )

    async def download_content(
        self,
        id_or_record: uuid.UUID | Storage,
        scope: ScopeContext | None = None,
    ) -> tuple[bytes, str, str]:
        """Download file content bytes directly from storage."""
        if isinstance(id_or_record, Storage):
            storage_record = id_or_record
        else:
            storage_record = await self.get_by_id(id_or_record, scope=scope)

        if not storage_record.is_uploaded:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File has not been uploaded yet.",
            )

        if storage_record.external_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="External URL resources must be accessed via download-url.",
            )

        data = await self.storage_provider.download(storage_record.file_key)
        return data, storage_record.name, storage_record.content_type

    async def download_zip(
        self,
        request: ZipDownloadRequest,
        scope: ScopeContext | None = None,
    ) -> tuple[bytes, str]:
        """Download multiple storage files bundled in a ZIP archive."""
        scope_filters = self.build_scope_filters(scope) if scope else []

        max_zip_files = await self.get_max_zip_file_count()
        if request.storage_ids:
            candidates = await self.storage_repo.find_uploaded_by_ids(
                request.storage_ids, scope_filters=scope_filters
            )
        elif request.entity_type and request.entity_id:
            candidates = await self.storage_repo.find_by_entity(
                request.entity_type,
                request.entity_id,
                limit=max_zip_files,
                scope_filters=scope_filters,
            )
        else:
            candidates = []

        accessible_items = [
            item
            for item in candidates
            if item.is_uploaded and item.status != RecordStatus.TRASHED
        ]

        if not accessible_items:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No accessible uploaded files found for criteria.",
            )

        max_zip_bytes = await self.get_max_zip_total_bytes()
        total_size = sum(item.size_bytes for item in accessible_items)
        if total_size > max_zip_bytes:
            max_mb = max_zip_bytes / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"Total size of files to zip ({total_size / (1024 * 1024):.1f} MB) "
                    f"exceeds maximum limit of {max_mb:.0f} MB"
                ),
            )

        zip_buffer = io.BytesIO()
        seen_names: dict[str, int] = {}

        with zipfile.ZipFile(
            zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED
        ) as zf:
            for item in accessible_items:
                count = seen_names.get(item.name, 0) + 1
                seen_names[item.name] = count
                filename = (
                    item.name
                    if count == 1
                    else f"{Path(item.name).stem}_{count}{Path(item.name).suffix}"
                )

                if item.external_url:
                    shortcut_data = (
                        f"[InternetShortcut]\r\nURL={item.external_url}\r\n"
                    ).encode()
                    shortcut_name = f"{sanitize_filename(Path(filename).stem)}.url"
                    zf.writestr(shortcut_name, shortcut_data)
                    continue

                try:
                    file_bytes = await self.storage_provider.download(item.file_key)
                except Exception as err:
                    logger.warning(f"Failed downloading {item.file_key} for zip: {err}")
                    continue

                zf.writestr(filename, file_bytes)

        return zip_buffer.getvalue(), DEFAULT_ZIP_FILENAME

    async def permanent_delete_storage(
        self,
        id: uuid.UUID,
        options: WriteOptions | None = None,
    ) -> None:
        """Permanently delete file from storage provider and database."""
        scope = options.scope if options else None
        user_id = options.user_id if options else None
        record = await self.get_by_id(id, scope=scope)

        if not record.external_url:
            try:
                await self.storage_provider.delete(record.file_key)
            except Exception as err:
                logger.warning(
                    f"Failed to delete storage file {record.file_key}: {err}"
                )

        if record.status != RecordStatus.TRASHED:
            await self.trash(id, user_id=user_id, scope=scope, options=options)
        await self.permanent_delete(id, scope=scope, options=options)

    async def purge_pending_orphans(self, older_than_seconds: int | None = None) -> int:
        """Purge unconfirmed pending uploads older than retention window."""
        if older_than_seconds is None:
            older_than_seconds = await self.get_orphan_retention_seconds()
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        return await self.storage_repo.purge_pending_orphans(cutoff)
