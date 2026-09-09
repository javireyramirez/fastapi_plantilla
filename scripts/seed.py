"""Master database seed runner orchestrating all domain seeders.

Usage:
    uv run python -m scripts.seed
    python scripts/seed.py
"""

import asyncio
import sys

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fastapi_plantilla.core.config import settings
from scripts.seeds.modules import seed_modules
from scripts.seeds.roles import seed_roles
from scripts.seeds.superadmin import seed_superadmin
from scripts.seeds.teams import seed_teams

__all__ = ["main", "run_all_seeds"]


async def run_all_seeds(session: AsyncSession) -> None:
    """Run all modular seeds sequentially within the provided session."""
    logger.info("🌱 Iniciando seed del sistema...")

    # 1. Modules
    logger.info("📦 [1/4] Creando/actualizando módulos del sistema...")
    module_map = await seed_modules(session)
    logger.info(f"   ✔ Módulos sincronizados: {len(module_map)}")

    # 2. Roles & Permissions
    logger.info("🛡️ [2/4] Creando/actualizando roles y permisos...")
    role_map = await seed_roles(session, module_map)
    logger.info(f"   ✔ Roles sincronizados: {len(role_map)}")

    # 3. SuperAdmin
    logger.info("👑 [3/4] Creando/actualizando usuario superadmin inicial...")
    superadmin = await seed_superadmin(session)
    if superadmin:
        logger.info(f"   ✔ Superadmin verificado: {superadmin.email}")
    else:
        logger.warning("   ⚠ Superadmin no configurado en variables de entorno")

    # 4. Teams & Role assignments
    logger.info("👥 [4/4] Creando/actualizando equipos globales y asignaciones...")
    team_map = await seed_teams(session, role_map, superadmin)
    logger.info(f"   ✔ Equipos sincronizados: {len(team_map)}")

    logger.info("✅ Seed completado exitosamente.\n")


async def main() -> None:
    """CLI entrypoint for standalone seed execution."""
    db_url = str(settings.db_url)
    engine = create_async_engine(db_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session, session.begin():
            await run_all_seeds(session)
    except Exception as exc:
        logger.error(f"❌ Error durante la ejecución del seed: {exc}")
        sys.exit(1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
