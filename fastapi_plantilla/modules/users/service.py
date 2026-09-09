import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, status
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
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
)

__all__ = ["UserAdminService"]

ph = PasswordHasher()


class UserAdminService(BaseAuditService[User]):
    """Business service governing administrative user management and lifecycle."""

    resource_name: str = "User"
    display_field: str = "name"

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

    async def _serialize_user(
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

    async def list_users(
        self,
        search: str | None = None,
        is_active: bool | None = None,
        is_super_admin: bool | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> PaginatedResponse[UserAdminResponse]:
        """Fetch paginated list of users for administration."""
        skip = (page - 1) * limit
        users = await self.repository.list_users(
            search=search,
            is_active=is_active,
            is_super_admin=is_super_admin,
            skip=skip,
            limit=limit,
        )
        total = await self.repository.count_users(
            search=search,
            is_active=is_active,
            is_super_admin=is_super_admin,
        )
        user_ids = [u.id for u in users]
        roles_map = await self.repository.get_roles_for_users(user_ids)
        data = [
            await self._serialize_user(u, roles=roles_map.get(u.id, [])) for u in users
        ]
        return PaginatedResponse(
            data=data,
            meta=PaginationMeta.create(page=page, limit=limit, total=total),
        )

    async def get_user(self, user_id: uuid.UUID) -> UserAdminResponse:
        """Fetch user details by ID."""
        user = await self._get_user_or_404(user_id)
        return await self._serialize_user(user)

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
        user = await self.create(payload, user_id=actor_id)

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
            user = await self.update(user_id, update_dict, user_id=actor_id)

        return await self._serialize_user(user)

    async def delete_user(
        self, user_id: uuid.UUID, actor_id: uuid.UUID | None = None
    ) -> None:
        """Soft-delete user into trash and terminate active sessions."""
        user = await self._get_user_or_404(user_id)
        self._check_not_system(user, "deleted")
        await self.trash(user_id, user_id=actor_id)
        await self.repository.invalidate_user_sessions(user_id)

    async def suspend_user(self, user_id: uuid.UUID) -> None:
        """Suspend user and immediately invalidate all their active sessions."""
        user = await self._get_user_or_404(user_id)
        self._check_not_system(user, "suspended")
        user.is_active = False
        await self.repository.invalidate_user_sessions(user.id)
        await self.repository.session.flush()

    async def reactivate_user(self, user_id: uuid.UUID) -> None:
        """Reactivate suspended user account."""
        user = await self._get_user_or_404(user_id)
        user.is_active = True
        await self.repository.session.flush()

    async def bulk_suspend(self, user_ids: list[uuid.UUID]) -> BulkResponse:
        """Suspend multiple users in bulk and terminate their active sessions."""
        count = await self.repository.bulk_set_active_status(user_ids, is_active=False)
        await self.repository.invalidate_bulk_sessions(user_ids)
        return BulkResponse(count=count, message=f"Suspended {count} users")

    async def bulk_reactivate(self, user_ids: list[uuid.UUID]) -> BulkResponse:
        """Reactivate multiple users in bulk."""
        count = await self.repository.bulk_set_active_status(user_ids, is_active=True)
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
