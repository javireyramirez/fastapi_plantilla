"""Catalog and synchronization of core system modules for RBAC and Audit."""

from collections.abc import Mapping, Sequence
from typing import Any, Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.rbac.models import SystemModule
from fastapi_plantilla.modules.rbac.schema import RbacActions

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
    supported_actions: list[RbacActions]
    requires_super_admin: bool
    show_in_nav: bool


CORE_SYSTEM_MODULES: Final[list[SystemModuleDefinition]] = [
    {
        "code": "companies",
        "name": "Compañías",
        "description": "Gestión de compañias / clientes",
        "category": "business",
        "category_name": "Negocio",
        "category_icon": "briefcase",
        "category_order": 1,
        "icon": "briefcase",
        "sort_order": 3,
        "is_active": True,
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
            RbacActions.RESTORE,
            RbacActions.EXPORT,
            RbacActions.IMPORT,
        ],
        "show_in_nav": True,
    },
    {
        "code": "storage",
        "name": "Almacenamiento",
        "description": "Gestión de archivos y almacenamiento del sistema",
        "category": "files",
        "category_name": "Archivos",
        "category_icon": "file-text",
        "category_order": 2,
        "icon": "hard-drive",
        "sort_order": 4,
        "is_active": True,
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
            RbacActions.RESTORE,
            RbacActions.EXPORT,
        ],
        "show_in_nav": True,
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
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
            RbacActions.RESTORE,
            RbacActions.EXPORT,
        ],
        "show_in_nav": True,
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
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
            RbacActions.RESTORE,
            RbacActions.SETTINGS,
        ],
        "show_in_nav": True,
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
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
            RbacActions.RESTORE,
        ],
        "show_in_nav": True,
    },
    {
        "code": "sessions",
        "name": "Sesiones",
        "description": "Control y gestión de sesiones de usuarios",
        "category": "security",
        "category_name": "Seguridad",
        "category_icon": "shield",
        "category_order": 3,
        "icon": "monitor",
        "sort_order": 3,
        "is_active": True,
        "supported_actions": [
            RbacActions.READ,
            RbacActions.DELETE,
            RbacActions.EXPORT,
        ],
        "show_in_nav": True,
    },
    {
        "code": "api_keys",
        "name": "Claves API",
        "description": "Gestión de claves de API para integraciones externas",
        "category": "security",
        "category_name": "Seguridad",
        "category_icon": "shield",
        "category_order": 3,
        "icon": "key",
        "sort_order": 4,
        "is_active": True,
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
            RbacActions.DELETE,
        ],
        "show_in_nav": True,
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
        "sort_order": 5,
        "is_active": True,
        "supported_actions": [
            RbacActions.READ,
            RbacActions.EXPORT,
        ],
        "show_in_nav": True,
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
        "sort_order": 6,
        "is_active": True,
        "supported_actions": [
            RbacActions.READ,
            RbacActions.DELETE,
            RbacActions.RESTORE,
        ],
        "show_in_nav": True,
    },
    {
        "code": "settings",
        "name": "Configuración",
        "description": "Configuración dinámica del sistema y feature flags",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "sliders",
        "sort_order": 7,
        "is_active": True,
        "supported_actions": [
            RbacActions.READ,
            RbacActions.UPDATE,
        ],
        "requires_super_admin": True,
        "show_in_nav": True,
    },
    {
        "code": "jobs",
        "name": "Tareas Asíncronas",
        "description": "Monitoreo y ejecución de background jobs del sistema",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "loader",
        "sort_order": 8,
        "is_active": True,
        "supported_actions": [
            RbacActions.CREATE,
            RbacActions.READ,
            RbacActions.UPDATE,
        ],
        "show_in_nav": True,
    },
    {
        "code": "notifications",
        "name": "Notificaciones",
        "description": "Notificaciones in-app y alertas en tiempo real",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "bell",
        "sort_order": 9,
        "is_active": True,
        "supported_actions": [],
        "show_in_nav": False,
    },
    {
        "code": "email_logs",
        "name": "Logs de Correo",
        "description": "Historial y trazabilidad de entrega de correos electrónicos",
        "category": "system",
        "category_name": "Sistema",
        "category_icon": "cpu",
        "category_order": 4,
        "icon": "mail",
        "sort_order": 10,
        "is_active": True,
        "supported_actions": [
            RbacActions.READ,
            RbacActions.EXPORT,
        ],
        "requires_super_admin": True,
        "show_in_nav": True,
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
    is_active, supported_actions, requires_super_admin and show_in_nav.
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
        requires_super_admin_val = bool(item.get("requires_super_admin", False))
        show_in_nav_val = bool(item.get("show_in_nav", True))
        raw_actions = item.get("supported_actions", [])
        actions_val = [a.value if hasattr(a, "value") else str(a) for a in raw_actions]

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
                supported_actions=actions_val,
                requires_super_admin=requires_super_admin_val,
                show_in_nav=show_in_nav_val,
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
            existing.supported_actions = actions_val
            existing.requires_super_admin = requires_super_admin_val
            existing.show_in_nav = show_in_nav_val
            synced.append(existing)

    await session.flush()
    return synced
