import uuid
from datetime import datetime

from fastapi import HTTPException, status

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.exporter import format_export
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    BulkIdsRequest,
    BulkResponse,
    ExportRequest,
    MessageResponse,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
    ScopeType,
)
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.auth.repository import AuthRepository
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.auth.utils import unsign_token
from fastapi_plantilla.modules.sessions.schema import (
    SessionAdminResponse,
    SessionPaginationParams,
)

__all__ = ["SessionAdminService"]


class SessionAdminService:
    """Business service governing administrative session management and audit."""

    def __init__(
        self,
        auth_repo: AuthRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self.auth_repo = auth_repo
        self.audit_repo = audit_repo

    def _resolve_allowed_user_ids(self, scope: ScopeContext) -> list[uuid.UUID] | None:
        """Resolve list of user IDs accessible within the caller's RBAC scope."""
        if scope.scope == ScopeType.GLOBAL:
            return None
        if scope.scope == ScopeType.TEAM:
            return (
                list(scope.teammate_ids)
                if scope.teammate_ids
                else ([scope.user_id] if scope.user_id else [])
            )
        if scope.scope == ScopeType.OWN:
            return [scope.user_id] if scope.user_id else []
        return []

    async def list_sessions(
        self,
        params: SessionPaginationParams,
        scope: ScopeContext,
        current_token: str | None = None,
    ) -> PaginatedResponse[SessionAdminResponse]:
        """List user sessions with pagination, filtering, and caller device check."""
        allowed_user_ids = self._resolve_allowed_user_ids(scope)

        sort_order_str = (
            params.sort_order.value
            if hasattr(params.sort_order, "value")
            else str(params.sort_order)
        )

        sessions, total = await self.auth_repo.list_sessions_paginated(
            page=params.page,
            limit=params.limit,
            sort_by=params.sort_by,
            sort_order=sort_order_str,
            user_id=params.user_id,
            is_valid=params.is_valid,
            search=params.search,
            created_at_from=params.created_at_from,
            created_at_to=params.created_at_to,
            expires_at_from=params.expires_at_from,
            expires_at_to=params.expires_at_to,
            allowed_user_ids=allowed_user_ids,
        )

        raw_token: str | None = None
        if current_token:
            raw_token = unsign_token(current_token, settings.auth_secret)

        items = [
            SessionAdminResponse(
                id=s.id,
                user_id=s.user_id,
                user_name=s.user.name if s.user else "Desconocido",
                user_email=s.user.email if s.user else "",
                ip_address=s.ip_address,
                user_agent=s.user_agent,
                is_valid=s.is_valid,
                impersonated_by=s.impersonated_by,
                is_impersonated=s.impersonated_by is not None,
                created_at=s.created_at,
                expires_at=s.expires_at,
                is_current=(raw_token is not None and s.token == raw_token),
            )
            for s in sessions
        ]

        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=items, meta=meta)

    async def get_session(
        self,
        session_id: uuid.UUID,
        scope: ScopeContext,
        current_token: str | None = None,
    ) -> SessionAdminResponse:
        """Fetch session detail by ID with RBAC scope validation."""
        session = await self.auth_repo.get_session_by_id_with_user(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada",
            )

        allowed_user_ids = self._resolve_allowed_user_ids(scope)
        if allowed_user_ids is not None and session.user_id not in allowed_user_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para acceder a sesiones fuera de tu ámbito",
            )

        raw_token: str | None = None
        if current_token:
            raw_token = unsign_token(current_token, settings.auth_secret)

        return SessionAdminResponse(
            id=session.id,
            user_id=session.user_id,
            user_name=session.user.name if session.user else "Desconocido",
            user_email=session.user.email if session.user else "",
            ip_address=session.ip_address,
            user_agent=session.user_agent,
            is_valid=session.is_valid,
            impersonated_by=session.impersonated_by,
            is_impersonated=session.impersonated_by is not None,
            created_at=session.created_at,
            expires_at=session.expires_at,
            is_current=(raw_token is not None and session.token == raw_token),
        )

    async def revoke_session(
        self,
        session_id: uuid.UUID,
        scope: ScopeContext,
        current_user: UserResponse,
        ip_address: str | None = None,
        user_agent: str | None = None,
        current_token: str | None = None,
    ) -> MessageResponse:
        """Revoke an active session enforcing RBAC scope and recording audit log."""
        session = await self.auth_repo.get_session_by_id_with_user(session_id)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada",
            )

        allowed_user_ids = self._resolve_allowed_user_ids(scope)
        if allowed_user_ids is not None and session.user_id not in allowed_user_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permiso para revocar sesiones fuera de tu ámbito",
            )

        if not session.is_valid:
            return MessageResponse(
                message="La sesión ya se encontraba revocada o inactiva",
            )

        revoked = await self.auth_repo.invalidate_session_admin(session_id)
        if not revoked:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada o ya revocada",
            )

        raw_token: str | None = None
        if current_token:
            raw_token = unsign_token(current_token, settings.auth_secret)
        is_current_session = bool(raw_token and session.token == raw_token)

        user_email = session.user.email if session.user else "Unknown"
        entry = AuditEntry(
            entity_type="session",
            entity_id=session.id,
            entity_name=f"Session {session.id} ({user_email})",
            action="REVOKE",
            actor_id=current_user.id,
            actor_name=current_user.name,
            actor_email=current_user.email,
            ip_address=ip_address,
            user_agent=user_agent,
            changes={"is_valid": {"old": True, "new": False}},
            details=f"Sesión revocada por {current_user.email}",
        )
        await self.audit_repo.record_entry(entry)

        detail_msg = (
            "Has revocado tu propia sesión actual; se cerrará tu sesión."
            if is_current_session
            else None
        )
        return MessageResponse(
            message="Sesión revocada exitosamente",
            detail=detail_msg,
        )

    async def bulk_revoke_sessions(
        self,
        req: BulkIdsRequest,
        scope: ScopeContext,
        current_user: UserResponse,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> BulkResponse:
        """Bulk revoke multiple active sessions with aggregated audit entry."""
        allowed_user_ids = self._resolve_allowed_user_ids(scope)
        revoked_ids = await self.auth_repo.bulk_invalidate_sessions_admin(
            session_ids=req.ids,
            allowed_user_ids=allowed_user_ids,
        )

        if revoked_ids:
            details_str = (
                f"Revocación masiva de {len(revoked_ids)} sesiones por "
                f"{current_user.email}"
            )
            entry = AuditEntry(
                entity_type="session",
                entity_id=None,
                entity_name=None,
                action="BULK_REVOKE",
                actor_id=current_user.id,
                actor_name=current_user.name,
                actor_email=current_user.email,
                ip_address=ip_address,
                user_agent=user_agent,
                changes={
                    "revoked_session_ids": [str(uid) for uid in revoked_ids],
                    "count": len(revoked_ids),
                },
                details=details_str,
            )
            await self.audit_repo.record_entry(entry)

        revoked_set = set(revoked_ids)
        unprocessed = [uid for uid in req.ids if uid not in revoked_set]

        return BulkResponse(
            count=len(revoked_ids),
            message=f"Se revocaron {len(revoked_ids)} sesiones correctamente",
            unprocessed_ids=unprocessed,
        )

    async def export_sessions(
        self,
        req: ExportRequest,
        scope: ScopeContext,
    ) -> tuple[bytes | str, str, str, int]:
        """Export session data in requested format."""
        allowed_user_ids = self._resolve_allowed_user_ids(scope)

        filters = req.filters or {}
        user_id_val = uuid.UUID(filters["user_id"]) if filters.get("user_id") else None
        is_valid_val = filters.get("is_valid")
        if isinstance(is_valid_val, str):
            is_valid_val = is_valid_val.lower() == "true"
        search_val = filters.get("search")

        created_at_from = (
            datetime.fromisoformat(filters["created_at_from"])
            if filters.get("created_at_from")
            else None
        )
        created_at_to = (
            datetime.fromisoformat(filters["created_at_to"])
            if filters.get("created_at_to")
            else None
        )
        expires_at_from = (
            datetime.fromisoformat(filters["expires_at_from"])
            if filters.get("expires_at_from")
            else None
        )
        expires_at_to = (
            datetime.fromisoformat(filters["expires_at_to"])
            if filters.get("expires_at_to")
            else None
        )

        sort_order_str = (
            req.sort_order.value
            if hasattr(req.sort_order, "value")
            else str(req.sort_order)
        )

        sessions = await self.auth_repo.get_sessions_for_export(
            ids=req.ids,
            user_id=user_id_val,
            is_valid=is_valid_val,
            search=search_val,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            expires_at_from=expires_at_from,
            expires_at_to=expires_at_to,
            allowed_user_ids=allowed_user_ids,
            sort_by=req.sort_by,
            sort_order=sort_order_str,
            limit=1000,
        )

        data = [
            {
                "id": str(s.id),
                "user_id": str(s.user_id),
                "user_name": s.user.name if s.user else "Desconocido",
                "user_email": s.user.email if s.user else "",
                "ip_address": s.ip_address or "",
                "user_agent": s.user_agent or "",
                "is_valid": "Activa" if s.is_valid else "Inactiva",
                "is_impersonated": "Sí" if s.impersonated_by else "No",
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "expires_at": s.expires_at.isoformat() if s.expires_at else "",
            }
            for s in sessions
        ]

        content, media_type, filename = format_export(
            format=req.format,
            data=data,
            slug="sessions",
            columns=req.columns,
        )
        return content, media_type, filename, len(sessions)
