import csv
import io
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status

from fastapi_plantilla.core.crud.schema import ExportFormat

try:
    import openpyxl  # type: ignore[import-not-found]
except ImportError:
    openpyxl = None  # type: ignore[assignment]

__all__ = [
    "export_delimited",
    "export_to_csv",
    "export_to_excel",
    "export_to_google_sheets",
    "export_to_json",
    "export_to_tsv",
    "format_export",
    "get_supported_export_formats",
    "serialize_cell",
]


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
