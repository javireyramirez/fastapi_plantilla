import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Query,
    status,
)

from fastapi_plantilla.core.crud.dependencies import (
    get_scope_context,
)
from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    BulkTrashActionRequest,
    TrashFilterParams,
    TrashItemResponse,
    TrashPurgeResponse,
)
from fastapi_plantilla.modules.trash.service import TrashService, get_trash_service

router = APIRouter(prefix="/trash", tags=["Trash"])


@router.get(
    "",
    response_model=PaginatedResponse[TrashItemResponse],
    summary="List paginated trash bin items with filtering and RBAC",
)
async def list_trash(
    params: Annotated[TrashFilterParams, Depends()],
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> PaginatedResponse[TrashItemResponse]:
    """Retrieve paginated list of items currently in the centralized trash bin."""
    return await service.list_trash(params, scope=scope)


@router.post(
    "/bulk/restore",
    response_model=BulkResponse,
    summary="Bulk restore multiple items from the trash bin",
)
async def bulk_restore_trash(
    request: BulkTrashActionRequest,
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Restore multiple items from trash bin to their respective modules."""
    return await service.bulk_restore(request, scope=scope, user_id=current_user.id)


@router.post(
    "/bulk/purge",
    response_model=BulkResponse,
    summary="Bulk permanently purge multiple items from trash and storage",
)
async def bulk_purge_trash(
    request: BulkTrashActionRequest,
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Permanently delete multiple items from their modules, storage, and trash."""
    return await service.bulk_purge(request, scope=scope, user_id=current_user.id)


@router.post(
    "/purge-expired",
    response_model=TrashPurgeResponse,
    summary="Trigger immediate purge of all expired trash records (SuperAdmin only)",
)
async def purge_expired_trash(
    limit: int = Query(default=DEFAULT_TRASH_PURGE_LIMIT, ge=1, le=5000),
    service: TrashService = Depends(get_trash_service),
    _: UserResponse = Depends(get_current_active_superuser),
) -> TrashPurgeResponse:
    """Execute administrative purge of records that passed retention deadline."""
    count = await service.purge_expired(limit=limit)
    return TrashPurgeResponse(
        purged_count=count,
        message=f"Successfully purged {count} expired items from the trash bin.",
    )


@router.get(
    "/{id}",
    response_model=TrashItemResponse,
    summary="Retrieve details of a single trash bin item",
)
async def get_trash_item(
    id: uuid.UUID,
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
) -> TrashItemResponse:
    """Fetch metadata of an item in the trash bin by primary key ID."""
    return await service.get_by_id(id, scope=scope)


@router.post(
    "/{id}/restore",
    response_model=TrashItemResponse,
    summary="Restore an item from trash back to active state in its origin module",
)
async def restore_trash_item(
    id: uuid.UUID,
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
    current_user: UserResponse = Depends(get_current_user),
) -> TrashItemResponse:
    """Restore soft-deleted entity to ACTIVE status and clear trash entry."""
    return await service.restore_item(id, scope=scope, user_id=current_user.id)


@router.delete(
    "/{id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently purge an item from source module, storage, and trash bin",
)
async def purge_trash_item(
    id: uuid.UUID,
    service: TrashService = Depends(get_trash_service),
    scope: ScopeContext = Depends(get_scope_context),
    current_user: UserResponse = Depends(get_current_user),
) -> None:
    """Permanently delete entity record and its associated storage files."""
    await service.purge_item(id, scope=scope, user_id=current_user.id)
