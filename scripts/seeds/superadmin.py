"""Initial superadmin user seeder."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.models import Account, User

__all__ = ["seed_superadmin"]

ph = PasswordHasher()


async def seed_superadmin(session: AsyncSession) -> User | None:
    """Seed or update the initial superadmin user based on environment variables.

    Returns:
        The seeded or updated superadmin User model, or None if unconfigured.
    """
    email = settings.initial_superadmin_email
    password = settings.initial_superadmin_password
    if not email or not password:
        logger.warning("Initial superadmin credentials not configured. Skipping seed.")
        return None

    stmt = select(User).where(User.email == email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            id=generate_uuid7(),
            name=settings.initial_superadmin_name,
            email=email,
            is_super_admin=True,
            is_active=True,
            email_verified=True,
        )
        session.add(user)
        await session.flush()

        account = Account(
            id=generate_uuid7(),
            user_id=user.id,
            provider_id="credential",
            account_id=email,
            password=ph.hash(password),
        )
        session.add(account)
        await session.flush()
        logger.info(f"Initial superadmin user seeded successfully: {email}")
    else:
        changed = False
        if not user.is_super_admin:
            user.is_super_admin = True
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if not user.email_verified:
            user.email_verified = True
            changed = True

        acc_stmt = select(Account).where(
            Account.user_id == user.id, Account.provider_id == "credential"
        )
        acc_result = await session.execute(acc_stmt)
        existing_account = acc_result.scalar_one_or_none()

        if existing_account is None:
            new_acc = Account(
                id=generate_uuid7(),
                user_id=user.id,
                provider_id="credential",
                account_id=email,
                password=ph.hash(password),
            )
            session.add(new_acc)
            changed = True
        else:
            try:
                if existing_account.password is None:
                    existing_account.password = ph.hash(password)
                    changed = True
                else:
                    ph.verify(existing_account.password, password)
            except VerifyMismatchError:
                existing_account.password = ph.hash(password)
                changed = True

        if changed:
            await session.flush()
            logger.info(f"Initial superadmin user verified/updated: {email}")

    return user
