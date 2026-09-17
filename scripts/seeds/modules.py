"""System modules seeder."""

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.rbac.catalog import (
    CORE_SYSTEM_MODULES,
    SystemModuleDefinition,
)
from fastapi_plantilla.modules.rbac.models import SystemModule

__all__ = ["SYSTEM_MODULES", "SystemModuleDef", "seed_modules"]

SystemModuleDef = SystemModuleDefinition
SYSTEM_MODULES: Final[list[SystemModuleDefinition]] = CORE_SYSTEM_MODULES


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
        requires_super_admin = bool(mod_def.get("requires_super_admin", False))
        show_in_nav = bool(mod_def.get("show_in_nav", True))
        raw_actions = mod_def.get("supported_actions", [])
        actions_val = [a.value if hasattr(a, "value") else str(a) for a in raw_actions]

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
                supported_actions=actions_val,
                requires_super_admin=requires_super_admin,
                show_in_nav=show_in_nav,
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
            record.supported_actions = actions_val
            record.requires_super_admin = requires_super_admin
            record.show_in_nav = show_in_nav
            await session.flush()

        module_map[mod_def["code"]] = record.id

    return module_map
