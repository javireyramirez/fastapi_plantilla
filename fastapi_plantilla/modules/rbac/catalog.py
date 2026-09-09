"""Catalog and synchronization of core system modules for RBAC and Audit."""

from collections.abc import Mapping, Sequence
from typing import Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.rbac.models import SystemModule

__all__ = [
    "CORE_SYSTEM_MODULES",
    "SystemModuleDefinition",
    "sync_system_modules",
]


class SystemModuleDefinition(TypedDict, total=False):
    """Metadata definition of a core system module."""

    code: str
    name: str
    description: str
    category: str
    icon: str | None
    sort_order: int


CORE_SYSTEM_MODULES: Final[list[SystemModuleDefinition]] = [
    {
        "code": "users",
        "name": "Usuarios",
        "description": "Gestión de usuarios",
        "category": "security",
        "icon": "users",
        "sort_order": 0,
    },
    {
        "code": "teams",
        "name": "Equipos",
        "description": "Gestión de equipos",
        "category": "security",
        "icon": "users-round",
        "sort_order": 1,
    },
    {
        "code": "roles",
        "name": "Roles",
        "description": "Gestión de roles y permisos",
        "category": "security",
        "icon": "shield",
        "sort_order": 2,
    },
    {
        "code": "companies",
        "name": "Empresas",
        "description": "Gestión de empresas / clientes",
        "category": "business",
        "icon": "briefcase",
        "sort_order": 3,
    },
    {
        "code": "documents",
        "name": "Documentos",
        "description": "Gestión de documentos",
        "category": "files",
        "icon": "file",
        "sort_order": 4,
    },
    {
        "code": "storage",
        "name": "Almacenamiento",
        "description": "Gestión de archivos",
        "category": "files",
        "icon": "hard-drive",
        "sort_order": 5,
    },
    {
        "code": "audit",
        "name": "Auditoría",
        "description": "Logs y auditoría",
        "category": "system",
        "icon": "activity",
        "sort_order": 6,
    },
    {
        "code": "trash",
        "name": "Papelera",
        "description": "Papelera de reciclaje y recuperación",
        "category": "system",
        "icon": "trash-2",
        "sort_order": 7,
    },
]


async def sync_system_modules(
    session: AsyncSession,
    modules: Sequence[SystemModuleDefinition | Mapping[str, str]] | None = None,
) -> list[SystemModule]:
    """Idempotently register and update system modules in the database.

    Ensures that all core system modules exist in `sys_modules`. If a module
    already exists by code, updates its human-readable name, description,
    category, icon and sort_order.
    """
    target_modules = modules or CORE_SYSTEM_MODULES
    synced: list[SystemModule] = []

    for item in target_modules:
        query = select(SystemModule).where(SystemModule.code == item["code"])
        result = await session.execute(query)
        existing = result.scalar_one_or_none()

        cat = str(item.get("category", "system"))
        ico = item.get("icon")
        icon_val = str(ico) if ico is not None else None
        sort_val = int(item.get("sort_order", 0))

        if existing is None:
            new_mod = SystemModule(
                code=item["code"],
                name=item["name"],
                description=item.get("description"),
                category=cat,
                icon=icon_val,
                sort_order=sort_val,
                is_active=True,
            )
            session.add(new_mod)
            synced.append(new_mod)
        else:
            existing.name = item["name"]
            existing.description = item.get("description")
            existing.category = cat
            existing.icon = icon_val
            existing.sort_order = sort_val
            synced.append(existing)

    await session.flush()
    return synced
