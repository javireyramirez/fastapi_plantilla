from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.modules.email.dependencies import (
    create_email_log_service,
    get_email_log_service,
)
from fastapi_plantilla.modules.email.schema import (
    EmailLogCreate,
    EmailLogPaginationParams,
    EmailLogResponse,
    EmailLogUpdate,
)

__all__ = ["router"]

router = create_crud_router(
    service_getter=get_email_log_service,
    schema_out=EmailLogResponse,
    schema_create=EmailLogCreate,
    schema_update=EmailLogUpdate,
    prefix="/email-logs",
    tags=["Email Logs"],
    resource_name="email_logs",
    pagination_params=EmailLogPaginationParams,
    service_factory=create_email_log_service,
    include_create=False,
    include_update=False,
    include_delete=False,
    include_trash=False,
    include_bulk=False,
    include_export=True,
    include_import=False,
)
