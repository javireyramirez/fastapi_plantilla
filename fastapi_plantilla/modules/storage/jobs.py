import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from fastapi_plantilla.core.crud.schema import ScopeContext
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.jobs.exceptions import JobCancelledError, JobError
from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.storage.dependencies import get_storage_provider
from fastapi_plantilla.modules.storage.models import Storage
from fastapi_plantilla.modules.storage.providers.base import StorageProvider
from fastapi_plantilla.modules.storage.repository import StorageRepository
from fastapi_plantilla.modules.storage.service import StorageService

__all__ = ["StorageCompressJobPayload", "handle_storage_compress"]


class StorageCompressJobPayload(BaseModel):
    """Payload to request asynchronous ZIP compression of storage files."""

    storage_ids: list[uuid.UUID] | None = None
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    zip_filename: str = "storage.zip"
    scope: dict[str, Any] | None = None


async def _archive_items_to_zip(
    tmp_path: Path,
    items: list[Storage],
    provider: StorageProvider,
    ctx: JobContext[StorageCompressJobPayload],
) -> None:
    """Download files and write into ZIP archive with cooperative cancellation check."""
    with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        total = len(items)
        for idx, item in enumerate(items, start=1):
            if await ctx.is_cancelled():
                raise JobCancelledError("ZIP compression was cancelled.")

            try:
                data = await provider.download(item.file_key)
                arcname = f"{item.id}_{item.name}" if item.name else item.file_key
                zf.writestr(arcname, data)
            except Exception as err:
                logger.warning(f"Failed downloading {item.file_key} for zip: {err}")

            pct = 10 + int((idx / total) * 75)
            await ctx.update_progress(pct, f"Compressed {idx}/{total} files...")


@register_job(
    "storage.compress",
    payload_model=StorageCompressJobPayload,
    title="Compresión de Archivos",
    description="Empaquetado y compresión streaming de documentos en archivo ZIP",
    category="files",
    icon="archive",
    is_dispatchable=False,
)
async def handle_storage_compress(
    ctx: JobContext[StorageCompressJobPayload],
) -> dict[str, Any]:
    """Execute background ZIP packaging with disk streaming to avoid OOM."""
    scope = ScopeContext.rehydrate(ctx.payload.scope)
    provider = get_storage_provider()
    repo = StorageRepository(ctx.session)
    settings_service = SystemSettingService(SystemSettingRepository(ctx.session))
    service = StorageService(repo, provider, settings_service=settings_service)

    await ctx.update_progress(5, "Resolving target documents for compression...")

    scope_filters = service.build_scope_filters(scope) if scope else []
    max_zip_files = await service.get_max_zip_file_count()
    if ctx.payload.storage_ids:
        candidates = await repo.find_uploaded_by_ids(
            ctx.payload.storage_ids, scope_filters=scope_filters
        )
    elif ctx.payload.entity_type and ctx.payload.entity_id:
        candidates = await repo.find_by_entity(
            ctx.payload.entity_type,
            ctx.payload.entity_id,
            limit=max_zip_files,
            scope_filters=scope_filters,
        )
    else:
        raise JobError("Either storage_ids or entity_type and entity_id required.")

    items = [
        item
        for item in candidates
        if item.is_uploaded and item.status != RecordStatus.TRASHED
    ]

    if not items:
        raise JobError("No storage documents found matching the specified criteria.")

    if len(items) > max_zip_files:
        raise JobError(
            f"File count ({len(items)}) exceeds max allowed {max_zip_files}."
        )

    total_size = sum(item.size_bytes for item in items if item.size_bytes)
    max_zip_bytes = await service.get_max_zip_total_bytes()
    if total_size > max_zip_bytes:
        max_mb = max_zip_bytes / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        raise JobError(
            f"Total size ({total_mb:.1f} MB) exceeds limit ({max_mb:.1f} MB)."
        )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)

    try:
        await _archive_items_to_zip(tmp_path, items, provider, ctx)

        await ctx.update_progress(88, "Uploading ZIP archive to storage...")
        file_key = f"temp_zips/{ctx.job_id}_{ctx.payload.zip_filename}"
        with tmp_path.open("rb") as f:
            zip_bytes = f.read()

        await provider.upload(file_key, zip_bytes, content_type="application/zip")

        try:
            download_url = await provider.get_presigned_url(file_key, expires_in=86400)
        except Exception:
            download_url = f"/api/storage/download?key={file_key}"

        await ctx.update_progress(100, f"ZIP ready: {ctx.payload.zip_filename}")

        return {
            "file_key": file_key,
            "filename": ctx.payload.zip_filename,
            "download_url": download_url,
            "size_bytes": len(zip_bytes),
            "file_count": len(items),
        }
    finally:
        tmp_path.unlink(missing_ok=True)
