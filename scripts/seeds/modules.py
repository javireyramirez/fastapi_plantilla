"""System modules seeder."""

import uuid
from typing import Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.rbac.models import SystemModule

__all__ = ["SYSTEM_MODULES", "SystemModuleDef", "seed_modules"]


class SystemModuleDef(TypedDict):
    """Metadata definition for a system module."""

    code: str
    name: str
    description: str
    icon: str
    sort_order: int
    category: str


SYSTEM_MODULES: Final[list[SystemModuleDef]] = [
    {
        "code": "users",
        "name": "Usuarios",
        "description": "Gestión de usuarios",
        "icon": "users",
        "sort_order": 0,
        "category": "security",
    },
    {
        "code": "teams",
        "name": "Equipos",
        "description": "Gestión de equipos",
        "icon": "users-round",
        "sort_order": 1,
        "category": "security",
    },
    {
        "code": "roles",
        "name": "Roles",
        "description": "Gestión de roles y permisos",
        "icon": "shield",
        "sort_order": 2,
        "category": "security",
    },
    {
        "code": "companies",
        "name": "Empresas",
        "description": "Gestión de empresas / clientes",
        "icon": "briefcase",
        "sort_order": 3,
        "category": "business",
    },
    {
        "code": "documents",
        "name": "Documentos",
        "description": "Gestión de documentos",
        "icon": "file",
        "sort_order": 4,
        "category": "files",
    },
    {
        "code": "storage",
        "name": "Almacenamiento",
        "description": "Gestión de archivos",
        "icon": "hard-drive",
        "sort_order": 5,
        "category": "files",
    },
    {
        "code": "audit",
        "name": "Auditoría",
        "description": "Logs y auditoría",
        "icon": "activity",
        "sort_order": 6,
        "category": "system",
    },
    {
        "code": "trash",
        "name": "Papelera",
        "description": "Papelera de reciclaje y recuperación",
        "icon": "trash-2",
        "sort_order": 7,
        "category": "system",
    },
]


async def seed_modules(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Idempotently seed system modules in the database.

    Returns:
        Mapping from module code to module UUID.
    """
    module_map: dict[str, uuid.UUID] = {}

    for mod_def in SYSTEM_MODULES:
        stmt = select(SystemModule).where(SystemModule.code == mod_def["code"])
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            record = SystemModule(
                id=generate_uuid7(),
                code=mod_def["code"],
                name=mod_def["name"],
                description=mod_def["description"],
                category=mod_def["category"],
                icon=mod_def["icon"],
                sort_order=mod_def["sort_order"],
                is_active=True,
            )
            session.add(record)
            await session.flush()
        else:
            record.name = mod_def["name"]
            record.description = mod_def["description"]
            record.category = mod_def["category"]
            record.icon = mod_def["icon"]
            record.sort_order = mod_def["sort_order"]
            record.is_active = True
            await session.flush()

        module_map[mod_def["code"]] = record.id

    return module_map
