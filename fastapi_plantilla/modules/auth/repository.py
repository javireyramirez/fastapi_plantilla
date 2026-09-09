import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.auth.models import (
    Account,
    Session,
    User,
    Verification,
)


class AuthRepository:
    """Repository for authentication and user data access."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # ==========================================
    # 1. User Operations
    # ==========================================

    async def create_user(
        self,
        name: str,
        email: str,
        image: str | None = None,
        email_verified: bool = False,
        is_active: bool = True,
        is_super_admin: bool = False,
        is_system: bool = False,
    ) -> User:
        """Create a new user in database using native Python types."""
        user = User(
            name=name,
            email=email,
            image=image,
            email_verified=email_verified,
            is_active=is_active,
            is_super_admin=is_super_admin,
            is_system=is_system,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        """Find user by email address."""
        query = select(User).where(User.email == email)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_user_by_id(self, user_id: uuid.UUID | str) -> User | None:
        """Find user by primary key ID."""
        query = select(User).where(User.id == user_id)
        result = await self.session.execute(query)
        return result.scalars().first()

    async def update_user_by_id(
        self,
        user_id: uuid.UUID | str,
        update_data: dict[str, Any],
    ) -> User | None:
        """Update user fields using a native Python dictionary."""
        user = await self.get_user_by_id(user_id=user_id)
        if not user:
            return None

        if not update_data:
            return user

        for field, value in update_data.items():
            if hasattr(user, field):
                setattr(user, field, value)

        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def delete_user_by_id(self, user_id: uuid.UUID | str) -> bool:
        """Delete user by ID and cascade delete associated records."""
        query = delete(User).where(User.id == user_id)
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount) > 0
        return False

    # ==========================================
    # 2. Account Operations
    # ==========================================

    async def create_account(
        self,
        user_id: uuid.UUID | str,
        provider_id: str,
        account_id: str,
        password_hash: str | None = None,
        **extra_fields: Any,
    ) -> Account:
        """Create credential or OAuth account linked to a user."""
        account = Account(
            user_id=user_id,
            provider_id=provider_id,
            account_id=account_id,
            password=password_hash,
            **extra_fields,
        )
        self.session.add(account)
        await self.session.flush()
        await self.session.refresh(account)
        return account

    async def get_account_by_provider(
        self, user_id: uuid.UUID | str, provider_id: str = "credential"
    ) -> Account | None:
        """Find account by user_id and provider name."""
        query = select(Account).where(
            Account.user_id == user_id,
            Account.provider_id == provider_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_user_by_provider_account(
        self, provider_id: str, account_id: str
    ) -> Account | None:
        """Find account by provider ID and external account ID."""
        query = select(Account).where(
            Account.account_id == account_id,
            Account.provider_id == provider_id,
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def update_account_tokens(
        self,
        account: Account,
        access_token: str | None = None,
        refresh_token: str | None = None,
        id_token: str | None = None,
        expires_at: datetime | None = None,
    ) -> Account:
        """Update OAuth tokens for an existing account."""
        if access_token is not None:
            account.access_token = access_token
        if refresh_token is not None:
            account.refresh_token = refresh_token
        if id_token is not None:
            account.id_token = id_token
        if expires_at is not None:
            account.access_token_expires_at = expires_at

        await self.session.flush()
        return account

    async def update_password_hash(
        self,
        user_id: uuid.UUID | str,
        new_password_hash: str,
        provider_id: str = "credential",
    ) -> bool:
        """Update password hash for a specific credential account."""
        account = await self.get_account_by_provider(
            user_id=user_id,
            provider_id=provider_id,
        )
        if not account:
            return False

        account.password = new_password_hash
        await self.session.flush()
        return True

    async def update_account_by_provider(
        self,
        user_id: uuid.UUID | str,
        provider_id: str,
        update_data: dict[str, Any],
    ) -> Account | None:
        """Update account fields using a native Python dictionary."""
        account = await self.get_account_by_provider(
            user_id=user_id,
            provider_id=provider_id,
        )
        if not account:
            return None

        if not update_data:
            return account

        for field, value in update_data.items():
            if hasattr(account, field):
                setattr(account, field, value)

        await self.session.flush()
        await self.session.refresh(account)
        return account

    # ==========================================
    # 3. Session Operations
    # ==========================================

    async def create_session(
        self,
        user_id: uuid.UUID | str,
        token: str,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
        impersonated_by: uuid.UUID | None = None,
    ) -> Session:
        """Create active session in database."""
        session = Session(
            user_id=user_id,
            token=token,
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
            is_valid=True,
            impersonated_by=impersonated_by,
        )
        self.session.add(session)
        await self.session.flush()
        await self.session.refresh(session)
        return session

    async def get_session_with_user(self, token: str) -> Session | None:
        """
        Get active session by token with user eagerly loaded.

        Validates that session is valid and not expired.
        """
        query = (
            select(Session)
            .options(joinedload(Session.user))
            .where(
                Session.token == token,
                Session.is_valid.is_(True),
                Session.expires_at > func.now(),
            )
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_active_user_sessions(self, user_id: uuid.UUID | str) -> list[Session]:
        """Fetch all active, non-expired sessions for a user."""
        query = (
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.is_valid.is_(True),
                Session.expires_at > func.now(),
            )
            .order_by(Session.created_at.desc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def invalidate_session(self, token: str) -> bool:
        """Invalidate single session by its raw token."""
        query = update(Session).where(Session.token == token).values(is_valid=False)
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount) > 0
        return False

    async def invalidate_session_by_id(
        self, session_id: uuid.UUID | str, user_id: uuid.UUID | str
    ) -> bool:
        """Invalidate a specific active session by its ID for a given user."""
        query = (
            update(Session)
            .where(
                Session.id == session_id,
                Session.user_id == user_id,
                Session.is_valid.is_(True),
            )
            .values(is_valid=False)
        )
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount) > 0
        return False

    async def invalidate_all_user_sessions(self, user_id: uuid.UUID | str) -> int:
        """Invalidate all active sessions of a user (Logout from all devices)."""
        query = (
            update(Session)
            .where(
                Session.user_id == user_id,
                Session.is_valid.is_(True),
            )
            .values(is_valid=False)
        )
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    async def invalidate_other_user_sessions(
        self, user_id: uuid.UUID | str, current_token: str
    ) -> int:
        """Invalidate all user sessions except the current active one."""
        query = (
            update(Session)
            .where(
                Session.user_id == user_id,
                Session.token != current_token,
                Session.is_valid.is_(True),
            )
            .values(is_valid=False)
        )
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    # ==========================================
    # 4. Verification Operations
    # ==========================================

    async def create_verification(
        self, identifier: str, value: str, expires_at: datetime
    ) -> Verification:
        """Create verification token/code (Email verification, Password reset)."""
        verification = Verification(
            identifier=identifier,
            value=value,
            expires_at=expires_at,
        )
        self.session.add(verification)
        await self.session.flush()
        await self.session.refresh(verification)
        return verification

    async def get_valid_verification(
        self, identifier: str, value: str
    ) -> Verification | None:
        """Find valid, non-expired verification record by identifier and value."""
        query = select(Verification).where(
            Verification.identifier == identifier,
            Verification.value == value,
            Verification.expires_at > func.now(),
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def get_valid_verification_by_value(self, value: str) -> Verification | None:
        """Find valid, non-expired verification record by its token value."""
        query = select(Verification).where(
            Verification.value == value,
            Verification.expires_at > func.now(),
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    async def delete_verification(self, identifier: str, value: str) -> bool:
        """Delete verification record after use (one-time token consumption)."""
        query = delete(Verification).where(
            Verification.identifier == identifier,
            Verification.value == value,
        )
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount) > 0
        return False

    # ==========================================
    # 5. Maintenance & Pruning
    # ==========================================

    async def delete_expired_sessions(self) -> int:
        """Delete expired sessions from database."""
        query = delete(Session).where(Session.expires_at < func.now())
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0

    async def delete_expired_verifications(self) -> int:
        """Delete expired verification records from database."""
        query = delete(Verification).where(Verification.expires_at < func.now())
        result = await self.session.execute(query)
        await self.session.flush()
        if isinstance(result, CursorResult):
            return int(result.rowcount)
        return 0
