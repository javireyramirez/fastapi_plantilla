import csv
import io
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status

from fastapi_plantilla.core.crud.schema import ExportFormat

try:
    import openpyxl  # type: ignore[import-not-found]
except ImportError:
    openpyxl = None  # type: ignore[assignment]

__all__ = [
    "EXPORT_STRATEGIES",
    "ExportStrategy",
    "export_to_csv",
    "export_to_excel",
    "export_to_google_sheets",
    "export_to_json",
    "export_to_tsv",
    "format_export",
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


def export_to_csv(data: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Serialize list of dictionaries to CSV string."""
    if not data:
        return ""
    fieldnames = columns if columns is not None else list(data[0].keys())
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in data:
        writer.writerow({k: serialize_cell(row.get(k)) for k in fieldnames})
    return output.getvalue()


def export_to_tsv(data: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """Serialize list of dictionaries to TSV (Tab Separated Values) string."""
    if not data:
        return ""
    fieldnames = columns if columns is not None else list(data[0].keys())
    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore"
    )
    writer.writeheader()
    for row in data:
        writer.writerow({k: serialize_cell(row.get(k)) for k in fieldnames})
    return output.getvalue()


def export_to_google_sheets(
    data: list[dict[str, Any]], columns: list[str] | None = None
) -> str:
    """
    Serialize data optimized for Google Sheets.

    Uses Tab-Separated Values (TSV) with a UTF-8 Byte Order Mark (BOM).
    This enables Google Sheets / Drive to automatically split columns and
    correctly render accents without encoding warnings.
    """
    tsv_content = export_to_tsv(data, columns)
    return f"\ufeff{tsv_content}" if tsv_content else ""


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


@dataclass(frozen=True)
class ExportStrategy:
    """Specification of an export format strategy."""

    extension: str
    media_type: str
    handler: Callable[[list[dict[str, Any]], list[str] | None], bytes | str]


EXPORT_STRATEGIES: dict[ExportFormat, ExportStrategy] = {
    ExportFormat.CSV: ExportStrategy(
        extension="csv",
        media_type="text/csv; charset=utf-8",
        handler=export_to_csv,
    ),
    ExportFormat.TSV: ExportStrategy(
        extension="tsv",
        media_type="text/tab-separated-values; charset=utf-8",
        handler=export_to_tsv,
    ),
    ExportFormat.GOOGLE_SHEETS: ExportStrategy(
        extension="tsv",
        media_type="text/tab-separated-values; charset=utf-8",
        handler=export_to_google_sheets,
    ),
    ExportFormat.JSON: ExportStrategy(
        extension="json",
        media_type="application/json",
        handler=export_to_json,
    ),
    ExportFormat.EXCEL: ExportStrategy(
        extension="xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        handler=export_to_excel,
    ),
}


def format_export(
    format: ExportFormat,
    data: list[dict[str, Any]],
    slug: str,
    columns: list[str] | None = None,
) -> tuple[bytes | str, str, str]:
    """
    Format dataset using the resolved ExportStrategy.

    Returns:
        (content, media_type, filename)
    """
    strategy = EXPORT_STRATEGIES.get(format)
    if not strategy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported export format: '{format}'",
        )

    content = strategy.handler(data, columns)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{timestamp}.{strategy.extension}"
    return content, strategy.media_type, filename
