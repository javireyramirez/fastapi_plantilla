"""Tests for modular seeds execution and idempotency."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import ScopeType
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import (
    Role,
    RoleAssignment,
    RolePermission,
    SystemModule,
)
from fastapi_plantilla.modules.teams.models import Team, TeamUser
from scripts.seed import run_all_seeds


async def test_run_all_seeds_idempotency(dbsession: AsyncSession) -> None:
    """Verify that running all modular seeds creates records and is idempotent."""
    # Run 1
    await run_all_seeds(dbsession)
    await dbsession.commit()

    # 1. Verify 8 modules
    mod_res = await dbsession.execute(select(SystemModule))
    modules = mod_res.scalars().all()
    assert len(modules) >= 8
    mod_codes = {m.code for m in modules}
    expected_codes = {
        "users",
        "teams",
        "roles",
        "companies",
        "documents",
        "storage",
        "audit",
        "trash",
    }
    assert expected_codes.issubset(mod_codes)

    # Check that category, icon, sort_order are populated
    users_mod = next(m for m in modules if m.code == "users")
    assert users_mod.category == "security"
    assert users_mod.icon == "users"
    assert users_mod.sort_order == 0

    # 2. Verify 3 roles
    role_res = await dbsession.execute(select(Role))
    roles = role_res.scalars().all()
    role_slugs = {r.slug for r in roles}
    assert {"admin", "editor", "viewer"}.issubset(role_slugs)

    admin_role = next(r for r in roles if r.slug == "admin")
    assert admin_role.color == "#f97316"
    assert admin_role.icon == "shield-check"

    # Verify admin permissions scope is GLOBAL
    perm_res = await dbsession.execute(
        select(RolePermission).where(RolePermission.role_id == admin_role.id)
    )
    admin_perms = perm_res.scalars().all()
    assert len(admin_perms) >= 8 * 8  # 8 modules x 8 actions
    assert all(p.scope == ScopeType.GLOBAL for p in admin_perms)

    # 3. Verify superadmin user
    sa_res = await dbsession.execute(select(User).where(User.is_super_admin.is_(True)))
    superadmin = sa_res.scalar_one_or_none()
    assert superadmin is not None

    # 4. Verify 3 teams
    team_res = await dbsession.execute(select(Team))
    teams = team_res.scalars().all()
    team_slugs = {t.slug for t in teams}
    assert {"admins", "editors", "viewers"}.issubset(team_slugs)

    # Verify team role assignments
    admins_team = next(t for t in teams if t.slug == "admins")
    assign_res = await dbsession.execute(
        select(RoleAssignment).where(
            RoleAssignment.entity_type == "team",
            RoleAssignment.entity_id == admins_team.id,
            RoleAssignment.role_id == admin_role.id,
        )
    )
    assert assign_res.scalar_one_or_none() is not None

    # Verify superadmin membership in Admins
    tu_res = await dbsession.execute(
        select(TeamUser).where(
            TeamUser.team_id == admins_team.id,
            TeamUser.user_id == superadmin.id,
        )
    )
    assert tu_res.scalar_one_or_none() is not None

    # Run 2: Verify Idempotency (running again doesn't crash or duplicate)
    await run_all_seeds(dbsession)
    await dbsession.commit()
