"""System settings seeder."""

from typing import Any, Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.settings.models import SystemSetting

__all__ = ["DEFAULT_SYSTEM_SETTINGS", "SettingDef", "seed_settings"]


class SettingDef(TypedDict, total=False):
    """Definition schema for system setting seed entries."""

    key: str
    value: Any
    description: str | None
    category: str
    is_public: bool


DEFAULT_SYSTEM_SETTINGS: Final[list[SettingDef]] = [
    {
        "key": "storage.max_upload_size_bytes",
        "value": 52428800,
        "description": "Tamaño máximo de subida en bytes (50 MB)",
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.allowed_mimetypes",
        "value": [
            "image/*",
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "text/csv",
        ],
        "description": "Tipos MIME permitidos para la subida de documentos",
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.allowed_extensions",
        "value": [
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "ppt",
            "pptx",
            "txt",
            "csv",
        ],
        "description": "Extensiones de archivo permitidas para el almacenamiento",
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.file_categories",
        "value": [
            {
                "code": "images",
                "name": "Imágenes",
                "icon": "image",
                "extensions": ["png", "jpg", "jpeg", "gif", "webp"],
                "mimes": [
                    "image/*",
                    "image/png",
                    "image/jpeg",
                    "image/gif",
                    "image/webp",
                ],
            },
            {
                "code": "documents",
                "name": "Documentos de Texto",
                "icon": "file-text",
                "extensions": ["pdf", "doc", "docx", "txt"],
                "mimes": [
                    "application/pdf",
                    "application/msword",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "text/plain",
                ],
            },
            {
                "code": "spreadsheets",
                "name": "Hojas de Cálculo",
                "icon": "file-spreadsheet",
                "extensions": ["xls", "xlsx", "csv"],
                "mimes": [
                    "application/vnd.ms-excel",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text/csv",
                ],
            },
            {
                "code": "presentations",
                "name": "Presentaciones",
                "icon": "presentation",
                "extensions": ["ppt", "pptx"],
                "mimes": [
                    "application/vnd.ms-powerpoint",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ],
            },
        ],
        "description": "Catálogo categorizado de tipos de archivo con iconos Lucide",
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "app.name",
        "value": "FastAPI Plantilla",
        "description": "Nombre de la aplicación",
        "category": "general",
        "is_public": True,
    },
    {
        "key": "app.maintenance_mode",
        "value": False,
        "description": "Activar modo mantenimiento en la plataforma",
        "category": "general",
        "is_public": True,
    },
]


async def seed_settings(session: AsyncSession) -> list[SystemSetting]:
    """Idempotently seed default system settings."""
    settings_list: list[SystemSetting] = []

    for item in DEFAULT_SYSTEM_SETTINGS:
        stmt = select(SystemSetting).where(SystemSetting.key == item["key"])
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            record = SystemSetting(
                id=generate_uuid7(),
                key=item["key"],
                value=item["value"],
                description=item.get("description"),
                category=item.get("category", "general"),
                is_public=item.get("is_public", False),
            )
            session.add(record)
            await session.flush()
        else:
            record.description = item.get("description")
            record.category = item.get("category", "general")
            record.is_public = item.get("is_public", False)
            await session.flush()

        settings_list.append(record)

    return settings_list
