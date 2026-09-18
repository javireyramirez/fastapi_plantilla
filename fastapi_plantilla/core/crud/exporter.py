import csv
import inspect
import io
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import ExportFormat, PaginationParams
from fastapi_plantilla.core.database import get_db_session

try:
    import openpyxl  # type: ignore[import-not-found]
except ImportError:
    openpyxl = None  # type: ignore[assignment]

__all__ = [
    "RESOURCE_EXPORT_REGISTRY",
    "ExportJobPayload",
    "ResourceExportDefinition",
    "dispatch_export_job",
    "export_delimited",
    "export_to_csv",
    "export_to_excel",
    "export_to_google_sheets",
    "export_to_json",
    "export_to_tsv",
    "format_export",
    "get_supported_export_formats",
    "register_export_resource",
    "resolve_export_db_session",
    "resolve_export_job_service",
    "resolve_export_settings_service",
    "serialize_cell",
    "should_run_export_async",
]


@dataclass(frozen=True)
class ResourceExportDefinition:
    """Metadata and service factory for exportable domain resources."""

    resource_name: str
    service_factory: Callable[[AsyncSession], Any]
    export_schema: type[BaseModel] | None = None
    pagination_params_class: type[PaginationParams] | None = None


RESOURCE_EXPORT_REGISTRY: dict[str, ResourceExportDefinition] = {}


def register_export_resource(
    resource_name: str,
    service_factory: Callable[[AsyncSession], Any],
    export_schema: type[BaseModel] | None = None,
    pagination_params_class: type[PaginationParams] | None = None,
) -> None:
    """Register a domain resource for async/background export generation."""
    RESOURCE_EXPORT_REGISTRY[resource_name.lower()] = ResourceExportDefinition(
        resource_name=resource_name.lower(),
        service_factory=service_factory,
        export_schema=export_schema,
        pagination_params_class=pagination_params_class,
    )


class ExportJobPayload(BaseModel):
    """Payload to execute an asynchronous export background job."""

    resource_name: str
    request: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, Any] | None = None


def serialize_cell(value: Any) -> Any:
    """Serialize values into primitives suitable for export."""
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)
    return value


def export_delimited(
    data: list[dict[str, Any]],
    columns: list[str] | None = None,
    delimiter: str = ",",
    prefix_bom: bool = False,
) -> str:
    """Serialize list of dictionaries to delimited text (CSV/TSV) string."""
    if not data and not columns:
        return ""
    fieldnames = columns if columns is not None else list(data[0].keys())
    output = io.StringIO()
    if prefix_bom:
        output.write("\ufeff")
    writer = csv.DictWriter(
        output, fieldnames=fieldnames, delimiter=delimiter, extrasaction="ignore"
    )
    writer.writeheader()
    for row in data:
        writer.writerow({k: serialize_cell(row.get(k)) for k in fieldnames})
    return output.getvalue()


def export_to_csv(data: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Serialize list of dictionaries to CSV string."""
    return export_delimited(data, columns, delimiter=",")


def export_to_tsv(data: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Serialize list of dictionaries to TSV (Tab Separated Values) string."""
    return export_delimited(data, columns, delimiter="\t")


def export_to_google_sheets(
    data: list[dict[str, Any]], columns: list[str] | None = None
) -> str:
    """Serialize data optimized for Google Sheets (TSV with UTF-8 BOM)."""
    return export_delimited(data, columns, delimiter="\t", prefix_bom=True)


def export_to_json(data: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Serialize list of dictionaries to formatted JSON string."""
    if columns is not None:
        filtered = [{k: serialize_cell(row.get(k)) for k in columns} for row in data]
    else:
        filtered = [{k: serialize_cell(v) for k, v in row.items()} for row in data]
    return json.dumps(filtered, indent=2, default=str)


def export_to_excel(
    data: list[dict[str, Any]], columns: list[str] | None = None
) -> bytes:
    """Serialize list of dictionaries to Excel XLSX bytes."""
    if openpyxl is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Excel export is not available. Please choose CSV, TSV, or JSON.",
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    fieldnames = (
        columns if columns is not None else (list(data[0].keys()) if data else [])
    )
    ws.append(fieldnames)
    for row in data:
        ws.append([serialize_cell(row.get(k)) for k in fieldnames])
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


_FORMAT_META: dict[ExportFormat, tuple[str, str]] = {
    ExportFormat.CSV: ("csv", "text/csv; charset=utf-8"),
    ExportFormat.TSV: ("tsv", "text/tab-separated-values; charset=utf-8"),
    ExportFormat.GOOGLE_SHEETS: ("tsv", "text/tab-separated-values; charset=utf-8"),
    ExportFormat.JSON: ("json", "application/json"),
    ExportFormat.EXCEL: (
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
}


def format_export(
    format: ExportFormat,
    data: list[dict[str, Any]],
    slug: str,
    columns: list[str] | None = None,
) -> tuple[bytes | str, str, str]:
    """Format dataset using resolved format into content, media_type, and filename."""
    meta = _FORMAT_META.get(format)
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export format: '{format}'",
        )

    extension, media_type = meta
    match format:
        case ExportFormat.CSV:
            content: bytes | str = export_to_csv(data, columns)
        case ExportFormat.TSV:
            content = export_to_tsv(data, columns)
        case ExportFormat.GOOGLE_SHEETS:
            content = export_to_google_sheets(data, columns)
        case ExportFormat.JSON:
            content = export_to_json(data, columns)
        case ExportFormat.EXCEL:
            content = export_to_excel(data, columns)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{timestamp}.{extension}"
    return content, media_type, filename


def get_supported_export_formats() -> list[str]:
    """Return all export formats supported by the system."""
    formats = [f.value for f in ExportFormat]
    if openpyxl is None and ExportFormat.EXCEL.value in formats:
        formats.remove(ExportFormat.EXCEL.value)
    return formats


async def resolve_export_db_session(request: Any, service: Any) -> AsyncSession | None:
    """Resolve an AsyncSession from dependency overrides, app state, or service repo."""
    app = getattr(request, "app", None)
    if app and get_db_session in app.dependency_overrides:
        override = app.dependency_overrides[get_db_session]
        res = override()
        if inspect.isasyncgen(res):
            return await anext(res)
        if inspect.isgenerator(res):
            return next(res)
        return await res if inspect.isawaitable(res) else res
    if hasattr(service, "repo") and getattr(service.repo, "session", None) is not None:
        return service.repo.session
    if app and hasattr(app.state, "db_session_factory"):
        return app.state.db_session_factory()
    return None


async def resolve_export_settings_service(request: Any, service: Any) -> Any:
    """Resolve SystemSettingService from dependency overrides or db session."""
    from fastapi_plantilla.modules.settings.dependencies import (  # noqa: PLC0415
        get_settings_service,
    )
    from fastapi_plantilla.modules.settings.repository import (  # noqa: PLC0415
        SystemSettingRepository,
    )
    from fastapi_plantilla.modules.settings.service import (  # noqa: PLC0415
        SystemSettingService,
    )

    app = getattr(request, "app", None)
    if app and get_settings_service in app.dependency_overrides:
        override = app.dependency_overrides[get_settings_service]
        res = override()
        return await res if inspect.isawaitable(res) else res

    sess = await resolve_export_db_session(request, service)
    if sess is not None:
        return SystemSettingService(SystemSettingRepository(sess))
    return None


async def resolve_export_job_service(request: Any, service: Any) -> Any:
    """Resolve JobService from dependency overrides or db session."""
    from fastapi_plantilla.modules.jobs.dependencies import (  # noqa: PLC0415
        get_job_service,
    )
    from fastapi_plantilla.modules.jobs.repository import (  # noqa: PLC0415
        JobRepository,
    )
    from fastapi_plantilla.modules.jobs.service import (  # noqa: PLC0415
        JobService,
    )

    app = getattr(request, "app", None)
    if app and get_job_service in app.dependency_overrides:
        override = app.dependency_overrides[get_job_service]
        res = override()
        return await res if inspect.isawaitable(res) else res

    sess = await resolve_export_db_session(request, service)
    if sess is not None:
        return JobService(JobRepository(sess))
    return None


async def should_run_export_async(
    *,
    async_job: bool,
    request: Any,
    service: Any,
    req: Any,
    scope: Any,
    pagination_params_class: Any,
) -> bool:
    """Determine if export should be executed asynchronously."""
    if async_job:
        return True
    with suppress(Exception):
        settings_service = await resolve_export_settings_service(request, service)
        if settings_service is not None and hasattr(service, "count"):
            raw_fmt = getattr(req, "format", "")
            fmt = getattr(raw_fmt, "value", str(raw_fmt)).lower()
            is_excel = fmt == "excel"
            threshold_key = (
                "exports.async_threshold_excel_rows"
                if is_excel
                else "exports.async_threshold_rows"
            )
            default_val = 2000 if is_excel else 5000
            threshold = await settings_service.get_value(
                threshold_key, default=default_val
            )
            total_rows = await service.count(
                req.filters,
                scope=scope,
                pagination_params_class=pagination_params_class,
            )
            return bool(total_rows > threshold)
    return False


async def dispatch_export_job(
    *,
    request: Any,
    service: Any,
    resource_name: str,
    req: Any,
    scope: Any,
) -> Any:
    """Enqueue an exports.generate background job."""
    from fastapi_plantilla.modules.jobs.schema import (  # noqa: PLC0415
        JobCreateRequest,
    )

    job_service = await resolve_export_job_service(request, service)
    if job_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JobService unavailable for asynchronous export",
        )

    job_payload = {
        "resource_name": resource_name,
        "request": req.model_dump(mode="json"),
        "scope": {
            "scope": scope.scope.value,
            "user_id": str(scope.user_id) if scope.user_id else None,
            "teammate_ids": [str(t) for t in scope.teammate_ids]
            if scope.teammate_ids
            else [],
            "is_super_admin": scope.is_super_admin,
        },
    }
    job = await job_service.enqueue(
        JobCreateRequest(
            name="exports.generate",
            payload=job_payload,
            entity_type=resource_name,
        ),
        created_by_id=scope.user_id,
    )
    repo = getattr(job_service, "repo", None)
    repo_session = getattr(repo, "session", None) if repo else None
    if repo_session is not None:
        await repo_session.commit()
    return job
