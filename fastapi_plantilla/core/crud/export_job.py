from typing import Any

from fastapi_plantilla.core.crud.exporter import (
    RESOURCE_EXPORT_REGISTRY,
    ExportJobPayload,
)
from fastapi_plantilla.core.crud.schema import ExportRequest, ScopeContext
from fastapi_plantilla.modules.jobs.exceptions import JobError
from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.storage.dependencies import get_storage_provider

__all__ = ["handle_exports_generate"]


@register_job(
    "exports.generate",
    payload_model=ExportJobPayload,
    title="Exportación de Datos",
    description="Generación asíncrona de archivos CSV, Excel, TSV o JSON",
    category="data",
    icon="download",
    is_dispatchable=False,
)
async def handle_exports_generate(ctx: JobContext[ExportJobPayload]) -> dict[str, Any]:
    """Execute asynchronous dataset export and stage result in storage."""
    resource_name = ctx.payload.resource_name.lower()
    reg = RESOURCE_EXPORT_REGISTRY.get(resource_name)
    if not reg:
        raise JobError(f"Resource '{resource_name}' is not registered for exports.")

    await ctx.update_progress(10, f"Initializing export for '{resource_name}'...")

    scope = ScopeContext.rehydrate(ctx.payload.scope)
    service = reg.service_factory(ctx.session)

    req = ExportRequest.model_validate(ctx.payload.request)

    await ctx.update_progress(30, "Querying and formatting dataset...")

    result = await service.export_data(
        req,
        scope=scope,
        export_schema=reg.export_schema,
        pagination_params_class=reg.pagination_params_class,
    )

    content, media_type, filename = result[0], result[1], result[2]
    data_bytes = content.encode("utf-8") if isinstance(content, str) else content

    await ctx.update_progress(70, "Uploading exported file to storage...")
    provider = get_storage_provider()
    file_key = f"exports/{ctx.job_id}_{filename}"
    await provider.upload(file_key, data_bytes, content_type=media_type)

    try:
        download_url = await provider.get_presigned_url(file_key, expires_in=86400)
    except Exception:
        download_url = f"/api/storage/download?key={file_key}"

    await ctx.update_progress(100, f"Export ready: {filename}")

    return {
        "file_key": file_key,
        "filename": filename,
        "download_url": download_url,
        "media_type": media_type,
        "size_bytes": len(data_bytes),
        "total_count": getattr(result, "total_count", None),
    }
