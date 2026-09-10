"""System modules seeder."""

import uuid
from typing import Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.rbac.models import SystemModule

__all__ = ["SYSTEM_MODULES", "SystemModuleDef", "seed_modules"]


class SystemModuleDef(TypedDict, total=False):
    """Metadata definition for a system module."""

    code: str
    name: str
    description: str
    category: str
    category_name: str
    category_icon: str | None
    category_order: int
    icon: str | None
    sort_order: int
    is_active: bool
    is_trasheable: bool


SYSTEM_MODULES: Final[list[SystemModuleDef]] = [
    {
        "code": "companies",
        "name": "Compañías",
        "description": "Gestión de empresas / clientes",
        "category": "business",
        "category_name": "Negocio",
        "category_icon": "briefcase",
        "category_order": 1,
        "icon": "briefcase",
        "sort_order": 3,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "documents",
        "name": "Documentos",
        "description": "Gestión de documentos",
        "category": "files",
        "category_name": "Archivos",
        "category_icon": "file-text",
        "category_order": 2,
        "icon": "file",
        "sort_order": 4,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "storage",
        "name": "Almacenamiento",
        "description": "Gestión de archivos",
        "category": "files",
        "category_name": "Archivos",
        "category_icon": "file-text",
        "category_order": 2,
        "icon": "hard-drive",
        "sort_order": 5,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "users",
        "name": "Usuarios",
        "description": "Gestión de usuarios",
        "category": "security",
        "category_name": "Seguridad",
        "category_icon": "shield",
        "category_order": 3,
        "icon": "users",
        "sort_order": 0,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "teams",
        "name": "Equipos",
        "description": "Gestión de equipos",
        "category": "security",
        "category_name": "Seguridad",
        "category_icon": "shield",
        "category_order": 3,
        "icon": "users-round",
        "sort_order": 1,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "roles",
        "name": "Roles",
        "description": "Gestión de roles y permisos",
        "category": "security",
        "category_name": "Seguridad",
        "category_icon": "shield",
        "category_order": 3,
        "icon": "shield",
        "sort_order": 2,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "rbac",
        "name": "Roles y Permisos",
        "description": "Control de acceso, roles y asignación de permisos",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": None,
        "sort_order": 0,
        "is_active": True,
        "is_trasheable": True,
    },
    {
        "code": "audit",
        "name": "Auditoría",
        "description": "Logs y auditoría",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "activity",
        "sort_order": 6,
        "is_active": True,
        "is_trasheable": False,
    },
    {
        "code": "trash",
        "name": "Papelera",
        "description": "Papelera de reciclaje y recuperación",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "trash-2",
        "sort_order": 7,
        "is_active": True,
        "is_trasheable": False,
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

        cat = mod_def["category"]
        cat_name = mod_def.get("category_name")
        cat_icon = mod_def.get("category_icon")
        cat_order = mod_def.get("category_order", 0)
        icon = mod_def.get("icon")
        sort_order = mod_def.get("sort_order", 0)
        is_active = mod_def.get("is_active", True)
        trasheable_val = mod_def.get("is_trasheable", True)

        if record is None:
            record = SystemModule(
                id=generate_uuid7(),
                code=mod_def["code"],
                name=mod_def["name"],
                description=mod_def.get("description"),
                category=cat,
                category_name=cat_name,
                category_icon=cat_icon,
                category_order=cat_order,
                icon=icon,
                sort_order=sort_order,
                is_active=is_active,
                is_trasheable=trasheable_val,
            )
            session.add(record)
            await session.flush()
        else:
            record.name = mod_def["name"]
            record.description = mod_def.get("description")
            record.category = cat
            record.category_name = cat_name
            record.category_icon = cat_icon
            record.category_order = cat_order
            record.icon = icon
            record.sort_order = sort_order
            record.is_active = is_active
            record.is_trasheable = trasheable_val
            await session.flush()

        module_map[mod_def["code"]] = record.id

    return module_map
