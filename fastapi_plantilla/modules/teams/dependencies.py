from fastapi import Depends

from fastapi_plantilla.modules.teams.service import TeamService

__all__ = ["get_team_service"]


def get_team_service(service: TeamService = Depends()) -> TeamService:
    """Dependency providing TeamService instance."""
    return service
