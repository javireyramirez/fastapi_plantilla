from fastapi_plantilla.modules.trash.listener import setup_trash_listeners
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.routes import router as trash_router
from fastapi_plantilla.modules.trash.service import (
    TrashService,
    register_trash_entity,
)
from fastapi_plantilla.modules.trash.tasks import (
    purge_expired_trash,
    run_periodic_trash_purge,
)

setup_trash_listeners()

__all__ = [
    "TrashItem",
    "TrashRepository",
    "TrashService",
    "purge_expired_trash",
    "register_trash_entity",
    "run_periodic_trash_purge",
    "setup_trash_listeners",
    "trash_router",
]
