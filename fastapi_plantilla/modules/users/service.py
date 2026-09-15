import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_audit import BaseAuditService
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.auth.models import Account, User, Verification
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.rbac.models import Role
from fastapi_plantilla.modules.rbac.repository import RbacRepository
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.users.repository import UserAdminRepository
from fastapi_plantilla.modules.users.schema import (
    UserAdminResponse,
    UserRoleAssignmentResponse,
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

    async def _ensure_not_last_superadmin(
        self, user_ids: Sequence[uuid.UUID], action: str = "deactivate"
    ) -> None:
        """Ensure deactivating or deleting users preserves at least one superadmin."""
        if not user_ids:
            return
        active_count = await self.repository.count_active_superadmins()
        if active_count - len(user_ids) < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot {action} the last active super administrator",
            )

    async def _get_user_or_404(
        self, user_id: uuid.UUID, allow_trashed: bool = False
    ) -> User:
        """Fetch user by ID or raise 404."""
        user = await self.repository.get_by_id(user_id)
        if not user or (not allow_trashed and user.status == RecordStatus.TRASHED):
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

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Any:
        """Restore user from trash bin and return enriched representation."""
        try:
            user = await super().restore(
                id, *where, user_id=user_id, scope=scope, options=options
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot restore user: email is already in use.",
            ) from exc
        return await self.serialize_user(user)

    async def on_after_trash(
        self,
        item: User,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Invalidate active sessions when user is soft-deleted."""
        await super().on_after_trash(item, user_id=user_id)
        await self.repository.invalidate_user_sessions(item.id)

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query clauses including name, email, and boolean status filters."""
        clauses = super().build_where_filters(params)
        name_val = getattr(params, "name", None)
        if name_val:
            clauses.append(self.build_string_filter("name", name_val))
        email_val = getattr(params, "email", None)
        if email_val:
            clauses.append(self.build_string_filter("email", email_val))
        for bool_field in (
            "is_active",
            "is_super_admin",
            "email_verified",
            "is_system",
        ):
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
        res = await super().find_paginated(
            params, *where, scope=scope, order_by=order_by
        )
        roles_map = await self.repository.get_roles_for_users([u.id for u in res.data])
        data = [
            await self.serialize_user(u, roles=roles_map.get(u.id, []))
            for u in res.data
        ]
        return PaginatedResponse(data=data, meta=res.meta)

    async def get_by_id(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> Any:
        """Fetch single user enriched with role assignments."""
        user = await super().get_by_id(id, *where, scope=scope)
        return await self.serialize_user(user)

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Any:
        """Administratively create a new user and assign initial roles."""
        create_data: dict[str, Any] = (
            data.model_dump() if isinstance(data, BaseModel) else dict(data)
        )
        email = str(create_data.get("email", "")).strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Email is required",
            )

        is_super = bool(create_data.get("is_super_admin", False))
        if is_super:
            is_actor_super = bool(scope and scope.is_super_admin)
            if user_id is not None and not is_actor_super:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only super administrators can grant super admin privileges",
                )

        if await self.repository.get_by_email(email):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{email}' already exists",
            )

        payload = {
            "name": create_data.get("name", ""),
            "email": email,
            "is_active": create_data.get("is_active", True),
            "is_super_admin": is_super,
            "email_verified": False,
            "is_system": False,
        }

        password = create_data.get("password")
        role_ids = create_data.get("role_ids", [])
        if role_ids:
            role_uuids = [uuid.UUID(str(r)) for r in role_ids]
            stmt = select(Role.id).where(
                Role.id.in_(role_uuids), Role.status != RecordStatus.TRASHED
            )
            valid_ids = set(
                (await self.repository.session.execute(stmt)).scalars().all()
            )
            missing = set(role_uuids) - valid_ids
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Roles not found: {[str(m) for m in missing]}",
                )

        try:
            async with self.repository.session.begin_nested():
                user = await super().create(
                    payload,
                    user_id=user_id,
                    owner_id=owner_id,
                    scope=scope,
                    allow_immutable=allow_immutable,
                    options=options,
                )
                if password:
                    self.repository.session.add(
                        Account(
                            user_id=user.id,
                            provider_id="credentials",
                            account_id=user.email,
                            password=ph.hash(password),
                        )
                    )
                    await self.repository.session.flush()

                for r_id in role_ids:
                    await self.rbac_repo.assign_role(
                        uuid.UUID(str(r_id)), "USER", user.id
                    )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"User with email '{email}' already exists",
            ) from exc

        return await self.serialize_user(user)

    async def _validate_superadmin_update(
        self,
        user: User,
        update_dict: dict[str, Any],
        actor_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> None:
        """Enforce superadmin permissions and protect the last active superadmin."""
        if "is_super_admin" in update_dict:
            is_actor_super_admin = bool(scope and scope.is_super_admin)
            if actor_id is not None and not is_actor_super_admin:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only super administrators can grant or revoke super admin "
                    "privileges",
                )
            if (
                user.is_super_admin
                and user.is_active
                and update_dict["is_super_admin"] is False
            ):
                await self._ensure_not_last_superadmin(
                    [user.id], action="revoke privileges from"
                )

        if (
            update_dict.get("is_active") is False
            and user.is_super_admin
            and user.is_active
        ):
            await self._ensure_not_last_superadmin([user.id], action="deactivate")

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
    ) -> Any:
        """Update user administrative attributes and enforce superadmin invariants."""
        user = await self._get_user_or_404(id, allow_trashed=allow_immutable)
        update_dict: dict[str, Any] = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )

        body_version = update_dict.pop("version", None)
        version_to_check = (
            expected_version if expected_version is not None else body_version
        )

        if user.is_system and ("is_active" in update_dict or "name" in update_dict):
            self._check_not_system(user, "modified")

        await self._validate_superadmin_update(
            user,
            update_dict,
            actor_id=uuid.UUID(str(user_id)) if user_id else None,
            scope=scope,
        )

        if "email" in update_dict:
            new_email = str(update_dict["email"]).strip().lower()
            if new_email != user.email:
                if await self.repository.get_by_email(new_email):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"User with email '{new_email}' already exists",
                    )
                update_dict["email"] = new_email
                update_dict["email_verified"] = False
            else:
                update_dict.pop("email")

        deactivating = update_dict.get("is_active") is False

        try:
            async with self.repository.session.begin_nested():
                if update_dict:
                    user = await super().update(
                        id,
                        update_dict,
                        *where,
                        expected_version=version_to_check,
                        user_id=user_id,
                        scope=scope,
                        allow_immutable=allow_immutable,
                        options=options,
                    )
                if deactivating:
                    await self.repository.invalidate_user_sessions(user.id)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            ) from exc

        if allow_immutable or not isinstance(data, BaseModel):
            return user

        return await self.serialize_user(user)

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
        if user.is_super_admin and user.is_active:
            await self._ensure_not_last_superadmin([user.id], action="delete")
        item = await super().delete(
            id, *where, user_id=user_id, scope=scope, options=options
        )
        return await self.serialize_user(item)

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
        await self._get_user_or_404(user_id)
        await self.bulk_suspend([user_id], user_id_actor=user_id_actor, options=options)

    async def reactivate_user(
        self,
        user_id: uuid.UUID,
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> None:
        """Reactivate suspended user account."""
        await self._get_user_or_404(user_id)
        await self.bulk_reactivate(
            [user_id], user_id_actor=user_id_actor, options=options
        )

    async def bulk_trash(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk trash users while protecting system users and superadmins."""
        users = await self.repository.find_many(User.id.in_(req.ids))
        for u in users:
            self._check_not_system(u, "deleted")

        superadmin_ids = [u.id for u in users if u.is_super_admin and u.is_active]
        await self._ensure_not_last_superadmin(superadmin_ids, action="delete")

        return await super().bulk_trash(
            req, *where, user_id=user_id, scope=scope, options=options
        )

    async def bulk_suspend(
        self,
        user_ids: list[uuid.UUID],
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Suspend multiple users in bulk and terminate their active sessions."""
        users = await self.repository.find_many(User.id.in_(user_ids))
        for u in users:
            self._check_not_system(u, "suspended")

        superadmin_ids = [u.id for u in users if u.is_super_admin and u.is_active]
        await self._ensure_not_last_superadmin(superadmin_ids, action="suspend")

        old_states = {u.id: u.is_active for u in users}
        count = await self.repository.bulk_set_active_status(user_ids, is_active=False)
        await self.repository.invalidate_bulk_sessions(user_ids)
        effective_actor = (options.user_id if options else None) or user_id_actor
        user_map = {u.id: u for u in users}
        for uid in user_ids:
            target_user = user_map.get(uid)
            old_active = old_states.get(uid, True)
            await self._emit_audit(
                item=target_user,
                action="SUSPEND",
                options=options,
                user_id=effective_actor,
                entity_id=uid,
                changes={"is_active": {"old": old_active, "new": False}},
                details="User account suspended via bulk action",
            )
        unprocessed = [uid for uid in user_ids if uid not in user_map]
        return BulkResponse(
            count=count,
            message=f"Suspended {count} users",
            unprocessed_ids=unprocessed,
        )

    async def bulk_reactivate(
        self,
        user_ids: list[uuid.UUID],
        user_id_actor: str | uuid.UUID | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Reactivate multiple users in bulk."""
        users = await self.repository.find_many(User.id.in_(user_ids))
        for u in users:
            self._check_not_system(u, "reactivated")

        old_states = {u.id: u.is_active for u in users}
        count = await self.repository.bulk_set_active_status(user_ids, is_active=True)
        effective_actor = (options.user_id if options else None) or user_id_actor
        user_map = {u.id: u for u in users}
        for uid in user_ids:
            target_user = user_map.get(uid)
            old_active = old_states.get(uid, False)
            await self._emit_audit(
                item=target_user,
                action="REACTIVATE",
                options=options,
                user_id=effective_actor,
                entity_id=uid,
                changes={"is_active": {"old": old_active, "new": True}},
                details="User account reactivated via bulk action",
            )
        unprocessed = [uid for uid in user_ids if uid not in user_map]
        return BulkResponse(
            count=count,
            message=f"Reactivated {count} users",
            unprocessed_ids=unprocessed,
        )

    async def assign_roles(
        self, user_id: uuid.UUID, role_ids: list[uuid.UUID]
    ) -> UserAdminResponse:
        """Assign multiple roles to a user."""
        user = await self._get_user_or_404(user_id)
        if role_ids:
            stmt = select(Role.id).where(
                Role.id.in_(role_ids), Role.status != RecordStatus.TRASHED
            )
            valid_ids = set(
                (await self.repository.session.execute(stmt)).scalars().all()
            )
            missing = set(role_ids) - valid_ids
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Roles not found: {[str(m) for m in missing]}",
                )
        for role_id in role_ids:
            await self.rbac_repo.assign_role(role_id, "USER", user.id)
        await self._emit_audit(
            item=user,
            action="ASSIGN_ROLES",
            details=f"Assigned roles {[str(r) for r in role_ids]} to user {user.email}",
        )
        return await self.serialize_user(user)

    async def remove_role(
        self, user_id: uuid.UUID, role_id: uuid.UUID
    ) -> UserAdminResponse:
        """Remove specific role assignment from a user."""
        user = await self._get_user_or_404(user_id)
        await self.rbac_repo.unassign_role(role_id, "USER", user.id)
        await self._emit_audit(
            item=user,
            action="UNASSIGN_ROLE",
            details=f"Removed role {role_id} from user {user.email}",
        )
        return await self.serialize_user(user)

    async def resend_invitation(self, user_id: uuid.UUID) -> None:
        """Generate verification token and send invitation / email confirmation."""
        user = await self._get_user_or_404(user_id)
        await self.repository.session.execute(
            delete(Verification).where(Verification.identifier == user.email)
        )
        token = secrets.token_urlsafe(32)
        exp = datetime.now(UTC) + timedelta(hours=24)
        self.repository.session.add(
            Verification(identifier=user.email, value=token, expires_at=exp)
        )
        await self.repository.session.flush()
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
        await self._emit_audit(
            item=user,
            action="RESEND_INVITATION",
            details=f"Resent invitation to user {user.email}",
        )

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
        user = await self._get_user_or_404(user_id)
        if team_ids:
            stmt = select(Team.id).where(
                Team.id.in_(team_ids), Team.status != RecordStatus.TRASHED
            )
            valid_ids = set(
                (await self.repository.session.execute(stmt)).scalars().all()
            )
            missing = set(team_ids) - valid_ids
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Teams not found: {[str(m) for m in missing]}",
                )
        count = await self.repository.assign_user_teams(user_id, team_ids)
        await self._emit_audit(
            item=user,
            action="ASSIGN_TEAMS",
            details=f"Assigned {count} teams to user {user.email}",
        )
        return BulkResponse(count=count, message=f"Successfully assigned {count} teams")

    async def remove_teams(
        self, user_id: uuid.UUID, team_ids: list[uuid.UUID]
    ) -> BulkResponse:
        """Remove multiple teams from a user."""
        user = await self._get_user_or_404(user_id)
        count = await self.repository.remove_user_teams(user_id, team_ids)
        await self._emit_audit(
            item=user,
            action="REMOVE_TEAMS",
            details=f"Removed {count} teams from user {user.email}",
        )
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
        user = await self._get_user_or_404(user_id)
        count = await self.repository.remove_user_roles_bulk(user_id, role_ids)
        await self._emit_audit(
            item=user,
            action="UNASSIGN_ROLES",
            details=f"Removed {count} roles from user {user.email}",
        )
        return BulkResponse(count=count, message=f"Successfully removed {count} roles")
