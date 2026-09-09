"""Roles and permissions seeder."""

import uuid
from typing import Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import ScopeType
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.rbac.models import Role, RolePermission
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["ROLES_DEF", "RoleDef", "seed_roles"]

ALL_ACTIONS: Final[list[RbacActions]] = [
    RbacActions.READ,
    RbacActions.CREATE,
    RbacActions.UPDATE,
    RbacActions.DELETE,
    RbacActions.RESTORE,
    RbacActions.EXPORT,
    RbacActions.IMPORT,
    RbacActions.SETTINGS,
]


class RolePermissionDef(TypedDict):
    """Permission configuration for a role."""

    actions: list[RbacActions]
    scope: ScopeType


class RoleDef(TypedDict):
    """Metadata definition for a system security role."""

    name: str
    slug: str
    description: str
    color: str
    icon: str
    is_system: bool
    permissions: RolePermissionDef


ROLES_DEF: Final[list[RoleDef]] = [
    {
        "name": "Admin",
        "slug": "admin",
        "description": "Acceso completo del sistema global o de equipo.",
        "color": "#f97316",
        "icon": "shield-check",
        "is_system": True,
        "permissions": {
            "actions": ALL_ACTIONS,
            "scope": ScopeType.GLOBAL,
        },
    },
    {
        "name": "Editor",
        "slug": "editor",
        "description": "Puede crear y editar recursos de sus equipos.",
        "color": "#3b82f6",
        "icon": "pencil",
        "is_system": True,
        "permissions": {
            "actions": [
                RbacActions.READ,
                RbacActions.CREATE,
                RbacActions.UPDATE,
                RbacActions.DELETE,
            ],
            "scope": ScopeType.TEAM,
        },
    },
    {
        "name": "Viewer",
        "slug": "viewer",
        "description": "Solo lectura sobre recursos asignados a sus equipos.",
        "color": "#6b7280",
        "icon": "eye",
        "is_system": True,
        "permissions": {
            "actions": [RbacActions.READ],
            "scope": ScopeType.TEAM,
        },
    },
]


async def seed_roles(
    session: AsyncSession,
    module_map: dict[str, uuid.UUID],
) -> dict[str, uuid.UUID]:
    """Idempotently seed system roles and their permission matrices.

    Args:
        session: Active async SQLAlchemy session.
        module_map: Mapping from module code to module UUID.

    Returns:
        Mapping from role slug to role UUID.
    """
    role_map: dict[str, uuid.UUID] = {}

    for role_def in ROLES_DEF:
        stmt = select(Role).where(Role.slug == role_def["slug"])
        result = await session.execute(stmt)
        role = result.scalar_one_or_none()

        if role is None:
            role = Role(
                id=generate_uuid7(),
                name=role_def["name"],
                slug=role_def["slug"],
                description=role_def["description"],
                color=role_def["color"],
                icon=role_def["icon"],
                is_system=role_def["is_system"],
            )
            session.add(role)
            await session.flush()
        else:
            role.name = role_def["name"]
            role.description = role_def["description"]
            role.color = role_def["color"]
            role.icon = role_def["icon"]
            role.is_system = role_def["is_system"]
            await session.flush()

        role_map[role_def["slug"]] = role.id

        # Seed role permissions across all known modules
        for module_id in module_map.values():
            for action in role_def["permissions"]["actions"]:
                perm_stmt = select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.module_id == module_id,
                    RolePermission.action == action,
                )
                perm_res = await session.execute(perm_stmt)
                perm = perm_res.scalar_one_or_none()

                if perm is None:
                    perm = RolePermission(
                        id=generate_uuid7(),
                        role_id=role.id,
                        module_id=module_id,
                        action=action,
                        scope=role_def["permissions"]["scope"],
                    )
                    session.add(perm)
                else:
                    perm.scope = role_def["permissions"]["scope"]

        await session.flush()

    return role_map
