import uuid
from typing import Annotated

from fastapi import Depends, status

from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.core.crud.schema import ScopeContext
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.companies.dependencies import (
    create_company_service,
    get_company_service,
)
from fastapi_plantilla.modules.companies.schema import (
    CompaniesPaginationParams,
    CompanyCreate,
    CompanyNotifyRequest,
    CompanyResponse,
    CompanyUpdate,
)
from fastapi_plantilla.modules.companies.service import CompanyService
from fastapi_plantilla.modules.notifications.dependencies import (
    get_notification_service,
)
from fastapi_plantilla.modules.notifications.schema import NotificationResponse
from fastapi_plantilla.modules.notifications.service import NotificationService
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["router"]

router = create_crud_router(
    service_getter=get_company_service,
    schema_out=CompanyResponse,
    schema_create=CompanyCreate,
    schema_update=CompanyUpdate,
    prefix="/companies",
    tags=["Companies"],
    resource_name="companies",
    pagination_params=CompaniesPaginationParams,
    service_factory=create_company_service,
)


@router.post(
    "/notify",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar notificación desde el módulo de compañías",
)
async def notify_company_general(
    payload: CompanyNotifyRequest,
    scope: Annotated[
        ScopeContext, Depends(require_permission("companies", RbacActions.READ))
    ],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    company_service: Annotated[CompanyService, Depends(get_company_service)],
    notification_service: Annotated[
        NotificationService, Depends(get_notification_service)
    ],
) -> NotificationResponse:
    """Send localized notification from companies module with optional company_id."""
    if payload.company_id is not None:
        return await company_service.notify_recipient(
            company_id=payload.company_id,
            recipient_id=payload.recipient_id,
            title=payload.title,
            comment=payload.comment,
            notification_service=notification_service,
            current_user=current_user,
            scope=scope,
            notification_type=payload.notification_type,
            action_url=payload.action_url,
            data=payload.data,
        )

    return await company_service.notify_general(
        recipient_id=payload.recipient_id,
        title=payload.title,
        comment=payload.comment,
        notification_service=notification_service,
        notification_type=payload.notification_type,
        action_url=payload.action_url,
        data=payload.data,
    )


@router.post(
    "/{id}/notify",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enviar notificación asociada a una compañía",
)
async def notify_company_recipient(
    id: uuid.UUID,
    payload: CompanyNotifyRequest,
    scope: Annotated[
        ScopeContext, Depends(require_permission("companies", RbacActions.READ))
    ],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    company_service: Annotated[CompanyService, Depends(get_company_service)],
    notification_service: Annotated[
        NotificationService, Depends(get_notification_service)
    ],
) -> NotificationResponse:
    """Send localized notification linked to a specific company ID."""
    return await company_service.notify_recipient(
        company_id=id,
        recipient_id=payload.recipient_id,
        title=payload.title,
        comment=payload.comment,
        notification_service=notification_service,
        current_user=current_user,
        scope=scope,
        notification_type=payload.notification_type,
        action_url=payload.action_url,
        data=payload.data,
    )
