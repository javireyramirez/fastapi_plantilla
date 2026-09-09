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


class SystemModuleDefinition(TypedDict):
    """Metadata definition of a core system module."""

    code: str
    name: str
    description: str


CORE_SYSTEM_MODULES: Final[list[SystemModuleDefinition]] = [
    {
        "code": "users",
        "name": "Usuarios",
        "description": "Gestión de usuarios, perfiles y estados de cuenta",
    },
    {
        "code": "teams",
        "name": "Equipos",
        "description": "Organizaciones, membresías y jerarquías de equipos",
    },
    {
        "code": "rbac",
        "name": "Roles y Permisos",
        "description": "Control de acceso, roles y asignación de permisos",
    },
    {
        "code": "audit",
        "name": "Auditoría",
        "description": "Trazabilidad de cambios, historial de eventos y accesos",
    },
    {
        "code": "storage",
        "name": "Almacenamiento",
        "description": "Gestión de documentos, adjuntos y archivos multi-cloud",
    },
]


async def sync_system_modules(
    session: AsyncSession,
    modules: Sequence[SystemModuleDefinition | Mapping[str, str]] | None = None,
) -> list[SystemModule]:
    """Idempotently register and update system modules in the database.

    Ensures that all core system modules exist in `sys_modules`. If a module
    already exists by code, updates its human-readable name and description
    while preserving the primary key and foreign key integrity.
    """
    target_modules = modules or CORE_SYSTEM_MODULES
    synced: list[SystemModule] = []

    for item in target_modules:
        query = select(SystemModule).where(SystemModule.code == item["code"])
        result = await session.execute(query)
        existing = result.scalar_one_or_none()

        if existing is None:
            new_mod = SystemModule(
                code=item["code"],
                name=item["name"],
                description=item["description"],
                is_active=True,
            )
            session.add(new_mod)
            synced.append(new_mod)
        else:
            if (
                existing.name != item["name"]
                or existing.description != item["description"]
            ):
                existing.name = item["name"]
                existing.description = item["description"]
            synced.append(existing)

    await session.flush()
    return synced
