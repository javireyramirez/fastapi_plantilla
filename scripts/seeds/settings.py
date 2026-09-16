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
        "description": "Tipos MIME permitidos para el almacenamiento de archivos",
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
        "key": "storage.max_zip_total_bytes",
        "value": 104857600,
        "description": "Tamaño acumulado máximo para empaquetado ZIP en bytes (100 MB)",
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.max_zip_file_count",
        "value": 100,
        "description": (
            "Número máximo de ficheros empaquetados por entidad en un archivo ZIP"
        ),
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.presigned_expiry_seconds",
        "value": 3600,
        "description": (
            "Tiempo de expiración en segundos para URLs presignadas de subida"
        ),
        "category": "storage",
        "is_public": True,
    },
    {
        "key": "storage.orphan_retention_seconds",
        "value": 86400,
        "description": "Tiempo de retención en segundos para purgar subidas pendientes",
        "category": "storage",
        "is_public": False,
    },
    {
        "key": "auth.password_reset_expiry_minutes",
        "value": 30,
        "description": (
            "Tiempo de expiración en minutos para tokens de recuperación de contraseña"
        ),
        "category": "auth",
        "is_public": False,
    },
    {
        "key": "auth.email_verification_expiry_hours",
        "value": 24,
        "description": (
            "Tiempo de expiración en horas para enlaces de verificación de email"
        ),
        "category": "auth",
        "is_public": False,
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
    {
        "key": "pagination.default_page_size",
        "value": 20,
        "description": "Número de elementos por página por defecto en tablas",
        "category": "pagination",
        "is_public": True,
    },
    {
        "key": "pagination.page_size_options",
        "value": [10, 20, 50, 100],
        "description": "Opciones de tamaño de página disponibles en tablas",
        "category": "pagination",
        "is_public": True,
    },
    {
        "key": "pagination.max_page_size",
        "value": 100,
        "description": "Límite máximo permitido de elementos por página",
        "category": "pagination",
        "is_public": True,
    },
    {
        "key": "trash.retention_days",
        "value": 30,
        "description": "Días de retención de elementos en la papelera antes de su purga automática",
        "category": "trash",
        "is_public": True,
    },
    {
        "key": "trash.purge_limit",
        "value": 500,
        "description": "Límite máximo de elementos purgados por lote en la papelera",
        "category": "trash",
        "is_public": False,
    },
    {
        "key": "trash.auto_purge_enabled",
        "value": True,
        "description": "Habilitar purga automática periódica de elementos caducados en la papelera",
        "category": "trash",
        "is_public": False,
    },
    {
        "key": "audit.retention_days",
        "value": 365,
        "description": "Días de retención de registros de auditoría antes de su purga automática",
        "category": "audit",
        "is_public": False,
    },
    {
        "key": "audit.purge_limit",
        "value": 1000,
        "description": "Límite máximo de registros de auditoría purgados por lote",
        "category": "audit",
        "is_public": False,
    },
    {
        "key": "audit.auto_purge_enabled",
        "value": True,
        "description": "Habilitar purga automática periódica de registros de auditoría caducados",
        "category": "audit",
        "is_public": False,
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
