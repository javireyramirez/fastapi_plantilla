"""Catalog and synchronization of core system modules for RBAC and Audit."""

from collections.abc import Mapping, Sequence
from typing import Any, Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.rbac.models import SystemModule

__all__ = [
    "CORE_SYSTEM_MODULES",
    "MODULE_CATEGORIES",
    "SystemModuleDefinition",
    "sync_system_modules",
]

MODULE_CATEGORIES: Final[dict[str, dict[str, Any]]] = {
    "business": {
        "name": "Negocio",
        "icon": "briefcase",
        "order": 1,
    },
    "files": {
        "name": "Archivos",
        "icon": "file-text",
        "order": 2,
    },
    "security": {
        "name": "Seguridad",
        "icon": "shield",
        "order": 3,
    },
    "system": {
        "name": "Sistema",
        "icon": "cpu",
        "order": 4,
    },
}


class SystemModuleDefinition(TypedDict, total=False):
    """Metadata definition of a core system module."""

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


CORE_SYSTEM_MODULES: Final[list[SystemModuleDefinition]] = [
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


async def sync_system_modules(
    session: AsyncSession,
    modules: Sequence[SystemModuleDefinition | Mapping[str, Any]] | None = None,
) -> list[SystemModule]:
    """Idempotently register and update system modules in the database.

    Ensures that all core system modules exist in `sys_modules`. If a module
    already exists by code, updates its human-readable name, description,
    category, category_name, category_icon, category_order, icon, sort_order,
    is_active and is_trasheable.
    """
    target_modules = modules or CORE_SYSTEM_MODULES
    synced: list[SystemModule] = []

    for item in target_modules:
        query = select(SystemModule).where(SystemModule.code == item["code"])
        result = await session.execute(query)
        existing = result.scalar_one_or_none()

        cat = str(item.get("category", "system"))
        cat_meta = MODULE_CATEGORIES.get(cat, {})
        cat_name = str(item.get("category_name") or cat_meta.get("name") or cat)
        cat_ico = item.get("category_icon") or cat_meta.get("icon")
        cat_icon = str(cat_ico) if cat_ico is not None else None
        cat_order = int(item.get("category_order") or cat_meta.get("order") or 0)

        ico = item.get("icon")
        icon_val = str(ico) if ico is not None else None
        sort_val = int(item.get("sort_order", 0))
        is_active_val = bool(item.get("is_active", True))
        trasheable_val = bool(item.get("is_trasheable", True))

        if existing is None:
            new_mod = SystemModule(
                code=str(item["code"]),
                name=str(item["name"]),
                description=str(item["description"])
                if item.get("description")
                else None,
                category=cat,
                category_name=cat_name,
                category_icon=cat_icon,
                category_order=cat_order,
                icon=icon_val,
                sort_order=sort_val,
                is_active=is_active_val,
                is_trasheable=trasheable_val,
            )
            session.add(new_mod)
            synced.append(new_mod)
        else:
            existing.name = str(item["name"])
            existing.description = (
                str(item["description"]) if item.get("description") else None
            )
            existing.category = cat
            existing.category_name = cat_name
            existing.category_icon = cat_icon
            existing.category_order = cat_order
            existing.icon = icon_val
            existing.sort_order = sort_val
            existing.is_active = is_active_val
            existing.is_trasheable = trasheable_val
            synced.append(existing)

    await session.flush()
    return synced
