import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.modules.auth.models import Account, User, Verification
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.rbac.repository import RbacRepository
from fastapi_plantilla.modules.users.repository import UserAdminRepository
from fastapi_plantilla.modules.users.schema import (
    UserAdminCreate,
    UserAdminResponse,
    UserAdminUpdate,
    UserRoleAssignmentResponse,
    UsersPaginationParams,
    UserTeamAssignmentResponse,
)

__all__ = ["UserAdminService"]

ph = PasswordHasher()


class UserAdminService(BaseAuditService[User]):
    """Business service governing administrative user management and lifecycle."""

    resource_name: str = "User"
    display_field: str = "name"
    search_fields: ClassVar[list[str]] = ["name", "email"]

    def __init__(
        self,
        repository: UserAdminRepository = Depends(),
        rbac_repository: RbacRepository = Depends(),
        email_service: EmailService = Depends(get_email_service),
    ) -> None:
        super().__init__(repository)
        self.repository: UserAdminRepository = repository
        self.rbac_repo = rbac_repository
        self.email_service = email_service

    def _check_not_system(self, user: User, action: str) -> None:
        if user.is_system:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"System user cannot be {action}"
            )

    async def _get_user_or_404(self, user_id: uuid.UUID) -> User:
        """Fetch user by ID or raise 404."""
        user = await self.repository.get_by_id(user_id)
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        return user

    async def serialize_user(
        self, user: User, roles: list[str] | None = None
    ) -> UserAdminResponse:
        """Enrich User model with assigned roles."""
        user_roles = (
            roles
            if roles is not None
            else await self.repository.get_user_roles(user.id)
        )
        res = UserAdminResponse.model_validate(user)
        res.roles = user_roles
        return res

    _serialize_user = serialize_user

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Any:
        """Restore user from trash bin and return enriched representation."""
        user = await super().restore(
            id, *where, user_id=user_id, scope=scope, options=options
        )
        return await self.serialize_user(user)

    async def restore_user(
        self,
        user_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> UserAdminResponse:
        """Restore user from trash bin (backward compatible alias)."""
        return await self.restore(user_id, user_id=actor_id, scope=scope)

    async def on_after_trash(
        self,
        item: User,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Invalidate active sessions when user is soft-deleted."""
        await super().on_after_trash(item, user_id=user_id)
        await self.repository.invalidate_user_sessions(item.id)

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query clauses including email search and boolean status filters."""
        clauses = super().build_where_filters(params)
        for bool_field in ("is_active", "is_super_admin", "email_verified"):
            val = getattr(params, bool_field, None)
            if val is not None:
                clause = self.build_boolean_filter(bool_field, val)
                if clause is not None:
                    clauses.append(clause)
        return clauses

    async def find_paginated(
        self,
        params: PaginationParams,
        *where: Any,
        scope: ScopeContext | None = None,
        order_by: Any = None,
    ) -> PaginatedResponse[Any]:
        """Fetch paginated users enriched with role assignments."""
        skip = (params.page - 1) * params.limit
        order_clause = self.build_order_by(params.sort_by, params.sort_order, order_by)
        where_clauses = (
            list(where)
            + self.build_where_filters(params)
            + self.build_scope_filters(scope)
        )
        users, total = await self.repository.find_many_with_count(
            *where_clauses, skip=skip, limit=params.limit, order_by=order_clause
        )
        user_ids = [u.id for u in users]
        roles_map = await self.repository.get_roles_for_users(user_ids)
        data = [
            await self.serialize_user(u, roles=roles_map.get(u.id, [])) for u in users
        ]
        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=data, meta=meta)

    async def list_users(
        self,
        search: str | None = None,
        is_active: bool | None = None,
        is_super_admin: bool | None = None,
        email_verified: bool | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        updated_at_from: datetime | None = None,
        updated_at_to: datetime | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[UserAdminResponse]:
        """Fetch paginated list of users for administration."""
        params = UsersPaginationParams(
            page=page,
            limit=limit,
            search=search,
            is_active=is_active,
            is_super_admin=is_super_admin,
            email_verified=email_verified,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            updated_at_from=updated_at_from,
            updated_at_to=updated_at_to,
        )
        return await self.find_paginated(params)

    async def get_by_id(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> Any:
        """Fetch single user enriched with role assignments."""
        user = await super().get_by_id(id, *where, scope=scope)
        return await self.serialize_user(user)

    get_user = get_by_id

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Any:
        """Create user administratively or via generic CRUD payload."""
        if isinstance(data, UserAdminCreate):
            actor_uuid = (
                uuid.UUID(str(user_id))
                if user_id
                else (scope.user_id if scope else None)
            )
            return await self.create_user(data, actor_id=actor_uuid)
        return await super().create(
            data,
            user_id=user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )

    async def create_user(
        self, data: UserAdminCreate, actor_id: uuid.UUID | None = None
    ) -> UserAdminResponse:
        """Administratively create a new user and assign initial roles."""
        if await self.repository.get_by_email(data.email):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"User with email '{data.email}' already exists",
            )

        payload = data.model_dump(
            include={"name", "email", "is_active", "is_super_admin"}
        )
        payload.update(email_verified=False, is_system=False)
        user = await super().create(payload, user_id=actor_id)

        if data.password:
            self.repository.session.add(
                Account(
                    user_id=user.id,
                    provider_id="credentials",
                    account_id=user.email,
                    password=ph.hash(data.password),
                )
            )
            await self.repository.session.flush()

        for role_id in data.role_ids:
            await self.rbac_repo.assign_role(role_id, "USER", user.id)

        return await self._serialize_user(user)

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
        allow_immutable: bool = False,
    ) -> Any:
        """Update user administratively or via generic CRUD payload."""
        if isinstance(data, UserAdminUpdate):
            actor_uuid = (
                uuid.UUID(str(user_id))
                if user_id
                else (scope.user_id if scope else None)
            )
            return await self.update_user(id, data, actor_id=actor_uuid)
        return await super().update(
            id,
            data,
            *where,
            expected_version=expected_version,
            user_id=user_id,
            scope=scope,
            options=options,
            allow_immutable=allow_immutable,
        )

    async def update_user(
        self,
        user_id: uuid.UUID,
        data: UserAdminUpdate,
        actor_id: uuid.UUID | None = None,
    ) -> UserAdminResponse:
        """Update user administrative attributes."""
        user = await self._get_user_or_404(user_id)
        update_dict = data.model_dump(exclude_unset=True)
        if "email" in update_dict:
            if update_dict["email"] != user.email:
                if await self.repository.get_by_email(update_dict["email"]):
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        f"User with email '{update_dict['email']}' already exists",
                    )
                update_dict["email_verified"] = False
            else:
                update_dict.pop("email")

        if update_dict.get("is_active") is False:
            await self.repository.invalidate_user_sessions(user.id)

        if update_dict:
            user = await super().update(user_id, update_dict, user_id=actor_id)

        return await self._serialize_user(user)

    async def delete(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Any:
        """Soft-delete user into trash and invalidate active sessions."""
        user = await self._get_user_or_404(id)
        self._check_not_system(user, "deleted")
        item = await super().delete(
            id, *where, user_id=user_id, scope=scope, options=options
        )
        return await self.serialize_user(item)

    async def delete_user(
        self, user_id: uuid.UUID, actor_id: uuid.UUID | None = None
    ) -> None:
        """Soft-delete user into trash and terminate active sessions."""
        await self.delete(user_id, user_id=actor_id)

    async def permanent_delete(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Any:
        """Permanently delete user from trash."""
        user = await self.repository.get_by_id(id)
        if user and user.is_system:
            self._check_not_system(user, "permanently deleted")
        deleted = await super().permanent_delete(
            id, *where, scope=scope, options=options
        )
        return await self.serialize_user(deleted, roles=[])

    async def suspend_user(
        self,
        user_id: uuid.UUID,
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> None:
        """Suspend user and immediately invalidate all their active sessions."""
        user = await self._get_user_or_404(user_id)
        self._check_not_system(user, "suspended")
        old_active = user.is_active
        user.is_active = False
        await self.repository.invalidate_user_sessions(user.id)
        await self.repository.session.flush()

        effective_actor = (options.user_id if options else None) or user_id_actor
        await self._emit_audit(
            item=user,
            action="SUSPEND",
            options=options,
            user_id=effective_actor,
            changes={"is_active": {"old": old_active, "new": False}},
            details=f"User {user.email} suspended and active sessions invalidated",
        )

    async def reactivate_user(
        self,
        user_id: uuid.UUID,
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> None:
        """Reactivate suspended user account."""
        user = await self._get_user_or_404(user_id)
        old_active = user.is_active
        user.is_active = True
        await self.repository.session.flush()

        effective_actor = (options.user_id if options else None) or user_id_actor
        await self._emit_audit(
            item=user,
            action="REACTIVATE",
            options=options,
            user_id=effective_actor,
            changes={"is_active": {"old": old_active, "new": True}},
            details=f"User {user.email} reactivated",
        )

    async def bulk_suspend(
        self,
        user_ids: list[uuid.UUID],
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Suspend multiple users in bulk and terminate their active sessions."""
        users = await self.repository.find_many(User.id.in_(user_ids))
        user_map = {u.id: u for u in users}
        count = await self.repository.bulk_set_active_status(user_ids, is_active=False)
        await self.repository.invalidate_bulk_sessions(user_ids)
        effective_actor = (options.user_id if options else None) or user_id_actor
        for uid in user_ids:
            u = user_map.get(uid)
            await self._emit_audit(
                item=u,
                action="SUSPEND",
                options=options,
                user_id=effective_actor,
                entity_id=uid,
                changes={"is_active": {"old": True, "new": False}},
                details="User account suspended via bulk action",
            )
        return BulkResponse(count=count, message=f"Suspended {count} users")

    async def bulk_reactivate(
        self,
        user_ids: list[uuid.UUID],
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Reactivate multiple users in bulk."""
        users = await self.repository.find_many(User.id.in_(user_ids))
        user_map = {u.id: u for u in users}
        count = await self.repository.bulk_set_active_status(user_ids, is_active=True)
        effective_actor = (options.user_id if options else None) or user_id_actor
        for uid in user_ids:
            u = user_map.get(uid)
            await self._emit_audit(
                item=u,
                action="REACTIVATE",
                options=options,
                user_id=effective_actor,
                entity_id=uid,
                changes={"is_active": {"old": False, "new": True}},
                details="User account reactivated via bulk action",
            )
        return BulkResponse(count=count, message=f"Reactivated {count} users")

    async def assign_roles(
        self, user_id: uuid.UUID, role_ids: list[uuid.UUID]
    ) -> UserAdminResponse:
        """Assign multiple roles to a user."""
        user = await self._get_user_or_404(user_id)
        for role_id in role_ids:
            await self.rbac_repo.assign_role(role_id, "USER", user.id)
        return await self._serialize_user(user)

    async def remove_role(
        self, user_id: uuid.UUID, role_id: uuid.UUID
    ) -> UserAdminResponse:
        """Remove specific role assignment from a user."""
        user = await self._get_user_or_404(user_id)
        await self.rbac_repo.unassign_role(role_id, "USER", user.id)
        return await self._serialize_user(user)

    async def resend_invitation(self, user_id: uuid.UUID) -> None:
        """Generate verification token and send invitation / email confirmation."""
        user = await self._get_user_or_404(user_id)
        token = secrets.token_urlsafe(32)
        exp = datetime.now(UTC) + timedelta(hours=24)
        self.repository.session.add(
            Verification(identifier=user.email, value=token, expires_at=exp)
        )
        if settings.frontend_url:
            link = str(
                (URL(settings.frontend_url) / "verify-email").with_query(token=token)
            )
            msg = (
                self.email_service.create_builder()
                .to(user.email)
                .subject("Invitación a la plataforma")
                .template("auth/verify_email.html", name=user.name, verify_link=link)
            )
            await self.email_service.send(msg)

    async def get_user_teams(
        self, user_id: uuid.UUID, page: int = 1, limit: int = 20
    ) -> PaginatedResponse[UserTeamAssignmentResponse]:
        """Fetch paginated list of teams assigned to user."""
        await self._get_user_or_404(user_id)
        skip = (page - 1) * limit
        items, total = await self.repository.get_user_team_assignments(
            user_id, skip=skip, limit=limit
        )
        return PaginatedResponse(
            data=[UserTeamAssignmentResponse.model_validate(it) for it in items],
            meta=PaginationMeta.create(page=page, limit=limit, total=total),
        )

    async def assign_teams(
        self, user_id: uuid.UUID, team_ids: list[uuid.UUID]
    ) -> BulkResponse:
        """Assign multiple teams to a user."""
        await self._get_user_or_404(user_id)
        count = await self.repository.assign_user_teams(user_id, team_ids)
        return BulkResponse(count=count, message=f"Successfully assigned {count} teams")

    async def remove_teams(
        self, user_id: uuid.UUID, team_ids: list[uuid.UUID]
    ) -> BulkResponse:
        """Remove multiple teams from a user."""
        await self._get_user_or_404(user_id)
        count = await self.repository.remove_user_teams(user_id, team_ids)
        return BulkResponse(count=count, message=f"Successfully removed {count} teams")

    async def get_user_roles_detailed(
        self, user_id: uuid.UUID, page: int = 1, limit: int = 20
    ) -> PaginatedResponse[UserRoleAssignmentResponse]:
        """Fetch paginated list of roles assigned to user with metadata."""
        await self._get_user_or_404(user_id)
        skip = (page - 1) * limit
        items, total = await self.repository.get_user_role_assignments(
            user_id, skip=skip, limit=limit
        )
        return PaginatedResponse(
            data=[UserRoleAssignmentResponse.model_validate(it) for it in items],
            meta=PaginationMeta.create(page=page, limit=limit, total=total),
        )

    async def remove_roles_bulk(
        self, user_id: uuid.UUID, role_ids: list[uuid.UUID]
    ) -> BulkResponse:
        """Remove multiple roles from a user in bulk."""
        await self._get_user_or_404(user_id)
        count = await self.repository.remove_user_roles_bulk(user_id, role_ids)
        return BulkResponse(count=count, message=f"Successfully removed {count} roles")
