from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.teams.repository import TeamRepository
from fastapi_plantilla.modules.teams.service import TeamService

__all__ = ["create_team_service", "get_team_service"]


def create_team_service(session: AsyncSession) -> TeamService:
    """Instantiate a TeamService bound directly to the given AsyncSession."""
    return TeamService(TeamRepository(session))


def get_team_service(service: TeamService = Depends()) -> TeamService:
    """Dependency providing TeamService instance."""
    return service
