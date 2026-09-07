from enum import StrEnum

__all__ = ["RbacActions"]


class RbacActions(StrEnum):
    """Enumeration of system RBAC actions."""

    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    RESTORE = "RESTORE"
    EXPORT = "EXPORT"
    IMPORT = "IMPORT"
    SETTINGS = "SETTINGS"
