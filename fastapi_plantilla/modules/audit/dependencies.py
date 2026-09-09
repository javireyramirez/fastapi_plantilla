from fastapi import Depends

from fastapi_plantilla.modules.audit.service import AuditService

__all__ = ["get_audit_service"]


def get_audit_service(service: AuditService = Depends()) -> AuditService:
    """Dependency providing an instance of AuditService."""
    return service
