import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import false, or_

from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    ScopeContext,
    ScopeType,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.core.database import Base

__all__ = ["BaseOwnedService"]


class BaseOwnedService[ModelT: Base](BaseAuditService[ModelT]):
    """Base business service enforcing multi-tenancy and RBAC ownership scopes."""

    mask_forbidden_as_not_found: bool = True
    owner_field: str = "owner_id"
    team_field: str = "team_id"

    # ==========================================
    # 1. RESOLUCIÓN DE SCOPE Y FILTROS
    # ==========================================

    def build_scope_filters(self, scope: ScopeContext | None = None) -> list[Any]:
        """Build query filter clauses based on RBAC scope context."""
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: security scope context is required",
            )

        scope_str = str(scope.scope).upper()

        if scope.is_super_admin or scope_str == ScopeType.GLOBAL:
            return []

        owner_col = self._get_column(self.owner_field)
        team_col = self._get_column(self.team_field)

        if scope_str == ScopeType.OWN:
            return (
                [owner_col == scope.user_id]
                if (owner_col is not None and scope.user_id is not None)
                else [false()]
            )

        if scope_str == ScopeType.TEAM:
            team_clauses: list[Any] = []
            if team_col is not None and scope.team_ids:
                team_clauses.append(team_col.in_(scope.team_ids))
            if owner_col is not None:
                if scope.teammate_ids:
                    team_clauses.append(owner_col.in_(scope.teammate_ids))
                elif scope.user_id is not None:
                    team_clauses.append(owner_col == scope.user_id)
            return [or_(*team_clauses) if team_clauses else false()]

        # Fail-Closed: any unknown or unsupported scope yields ZERO records
        return [false()]

    # ==========================================
    # 2. LECTURA ATÓMICA CON DISCRIMINACIÓN 404/403
    # ==========================================

    async def get_by_id(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Fetch a single record by ID enforcing scope authorization."""
        scope_filters = list(where) + self.build_scope_filters(scope)
        if not scope_filters:
            return await super().get_by_id(id, *where, scope=scope)

        item = await self.repository.find_first(
            self.repository.pk == id, *scope_filters
        )
        if item is None:
            await self._raise_not_found_or_forbidden(id)
        return item

    # ==========================================
    # 3. AUTORIZACIÓN SIMÉTRICA Y ESCRITURAS
    # ==========================================

    def can_assign_team(
        self, target_team_id: uuid.UUID, scope: ScopeContext | None
    ) -> bool:
        """Check whether the client is authorized to assign a record to the team."""
        if scope is None:
            return False
        if scope.is_super_admin or str(scope.scope).upper() == ScopeType.GLOBAL:
            return True
        scope_team_ids = {self._to_uuid(tid) for tid in scope.team_ids}
        return target_team_id in scope_team_ids

    def can_reassign_owner(
        self, target_owner_id: uuid.UUID, scope: ScopeContext | None
    ) -> bool:
        """Check whether the client is authorized to reassign the record owner."""
        if scope is None:
            return False
        if scope.is_super_admin or str(scope.scope).upper() == ScopeType.GLOBAL:
            return True
        # Self-claiming: an authenticated user claiming or setting themselves
        if scope.user_id is not None and target_owner_id == self._to_uuid(
            scope.user_id
        ):
            return True
        # Reassignment to a verified teammate within TEAM scope
        if str(scope.scope).upper() == ScopeType.TEAM and scope.teammate_ids:
            teammate_uuids = {self._to_uuid(uid) for uid in scope.teammate_ids}
            return target_owner_id in teammate_uuids
        return False

    def _resolve_owner_and_team(
        self,
        payload: dict[str, Any],
        user_id: str | uuid.UUID | None,
        owner_id: uuid.UUID | None,
        scope: ScopeContext | None,
    ) -> None:
        """Resolve and validate owner_id and team_id for a record payload."""
        has_owner = self._get_column(self.owner_field) is not None
        has_team = self._get_column(self.team_field) is not None

        if has_owner:
            target_owner_id = self._to_uuid(payload.get(self.owner_field))
            if target_owner_id is not None and self.can_reassign_owner(
                target_owner_id, scope
            ):
                payload[self.owner_field] = target_owner_id
            else:
                resolved_owner = owner_id or self._to_uuid(user_id)
                if resolved_owner is not None:
                    payload[self.owner_field] = resolved_owner
                else:
                    payload.pop(self.owner_field, None)

        if has_team:
            target_team_id = self._to_uuid(payload.get(self.team_field))
            if target_team_id is not None:
                if scope is not None and not self.can_assign_team(
                    target_team_id, scope
                ):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: cannot assign record to the specified team",
                    )
                payload[self.team_field] = target_team_id
            elif scope and len(scope.team_ids) == 1:
                payload[self.team_field] = scope.team_ids[0]

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Create a record assigning owner and team stamping."""
        effective_user_id = (options.user_id if options else None) or user_id
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        self._resolve_owner_and_team(
            payload, user_id=effective_user_id, owner_id=owner_id, scope=scope
        )
        return await super().create(
            data=payload,
            user_id=effective_user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Update a record verifying ownership and team assignment permissions."""
        effective_user_id = (options.user_id if options else None) or user_id
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )

        has_owner = self._get_column(self.owner_field) is not None
        if has_owner and self.owner_field in payload:
            target_owner_id = self._to_uuid(payload[self.owner_field])
            if target_owner_id is not None:
                if not self.can_reassign_owner(target_owner_id, scope):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: cannot transfer ownership to this user",
                    )
                payload[self.owner_field] = target_owner_id
            else:
                is_admin = bool(
                    scope
                    and (
                        scope.is_super_admin
                        or str(scope.scope).upper() == ScopeType.GLOBAL
                    )
                )
                if scope is not None and not is_admin:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: cannot unassign record owner",
                    )

        has_team = self._get_column(self.team_field) is not None
        if has_team and self.team_field in payload:
            target_team_id = self._to_uuid(payload[self.team_field])
            if target_team_id is not None:
                if scope is not None and not self.can_assign_team(
                    target_team_id, scope
                ):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: cannot assign record to the specified team",
                    )
                payload[self.team_field] = target_team_id
            else:
                is_admin = bool(
                    scope
                    and (
                        scope.is_super_admin
                        or str(scope.scope).upper() == ScopeType.GLOBAL
                    )
                )
                if scope is not None and not is_admin:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Forbidden: cannot unassign record team",
                    )

        return await super().update(
            id,
            payload,
            *where,
            expected_version=expected_version,
            user_id=effective_user_id,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )

    async def bulk_create(
        self,
        items: Sequence[BaseModel | dict[str, Any]],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk create multiple records assigning actor, owner, and team stamping."""
        effective_user_id = (options.user_id if options else None) or user_id
        stamped_items: list[dict[str, Any]] = []
        for item in items:
            d = item.model_dump() if isinstance(item, BaseModel) else dict(item)
            self._resolve_owner_and_team(
                d, user_id=effective_user_id, owner_id=owner_id, scope=scope
            )
            stamped_items.append(d)
        return await super().bulk_create(
            stamped_items,
            user_id=effective_user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )
