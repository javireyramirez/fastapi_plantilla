"""Global teams and role assignments seeder."""

import uuid
from typing import Final, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.rbac.models import RoleAssignment
from fastapi_plantilla.modules.teams.models import Team, TeamUser

__all__ = ["TEAMS_DEF", "TeamDef", "seed_teams"]


class TeamDef(TypedDict):
    """Metadata definition for default system teams."""

    name: str
    slug: str
    description: str
    role_slug: str


TEAMS_DEF: Final[list[TeamDef]] = [
    {
        "name": "Admins",
        "slug": "admins",
        "description": "Equipo de administradores del sistema",
        "role_slug": "admin",
    },
    {
        "name": "Editors",
        "slug": "editors",
        "description": "Equipo de editores y operadores",
        "role_slug": "editor",
    },
    {
        "name": "Viewers",
        "slug": "viewers",
        "description": "Equipo de visualizadores con acceso de solo lectura",
        "role_slug": "viewer",
    },
]


async def seed_teams(
    session: AsyncSession,
    role_map: dict[str, uuid.UUID],
    superadmin: User | None = None,
) -> dict[str, uuid.UUID]:
    """Idempotently seed default teams, assign roles, and enroll superadmin in Admins.

    Args:
        session: Active async SQLAlchemy session.
        role_map: Mapping from role slug to role UUID.
        superadmin: Optional superadmin User to assign as owner/member.

    Returns:
        Mapping from team slug to team UUID.
    """
    team_map: dict[str, uuid.UUID] = {}

    for team_def in TEAMS_DEF:
        stmt = select(Team).where(Team.slug == team_def["slug"])
        result = await session.execute(stmt)
        team = result.scalar_one_or_none()

        if team is None:
            team = Team(
                id=generate_uuid7(),
                name=team_def["name"],
                slug=team_def["slug"],
                description=team_def["description"],
                owner_id=superadmin.id if superadmin else None,
            )
            session.add(team)
            await session.flush()
        else:
            team.name = team_def["name"]
            team.description = team_def["description"]
            if superadmin and team.owner_id is None:
                team.owner_id = superadmin.id
            await session.flush()

        team_map[team_def["slug"]] = team.id

        # Assign system role to team in rbac_role_assignments
        role_id = role_map.get(team_def["role_slug"])
        if role_id is not None:
            assign_stmt = select(RoleAssignment).where(
                RoleAssignment.role_id == role_id,
                RoleAssignment.entity_type == "team",
                RoleAssignment.entity_id == team.id,
            )
            assign_res = await session.execute(assign_stmt)
            assignment = assign_res.scalar_one_or_none()

            if assignment is None:
                assignment = RoleAssignment(
                    id=generate_uuid7(),
                    role_id=role_id,
                    entity_type="team",
                    entity_id=team.id,
                )
                session.add(assignment)
                await session.flush()

    # Enroll superadmin in the Admins team
    if superadmin and "admins" in team_map:
        admins_team_id = team_map["admins"]
        admin_role_id = role_map.get("admin")

        tu_stmt = select(TeamUser).where(
            TeamUser.team_id == admins_team_id,
            TeamUser.user_id == superadmin.id,
        )
        tu_res = await session.execute(tu_stmt)
        membership = tu_res.scalar_one_or_none()

        if membership is None:
            membership = TeamUser(
                id=generate_uuid7(),
                team_id=admins_team_id,
                user_id=superadmin.id,
                role_id=admin_role_id,
            )
            session.add(membership)
            await session.flush()

    return team_map
