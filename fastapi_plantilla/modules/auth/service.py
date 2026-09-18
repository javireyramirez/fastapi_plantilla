import contextlib
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from loguru import logger
from sqlalchemy.exc import IntegrityError
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.crud.service_audit import (
    dispatch_audit_event,
    dispatch_trash_hook,
)
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.audit.schema import AuditAction
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.oauth import GoogleOAuthClient
from fastapi_plantilla.modules.auth.repository import AuthRepository
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    ChangeEmailInput,
    DeleteAccountInput,
    ForgotPasswordRequest,
    MagicLinkRequest,
    OAuthUserInfo,
    PasswordChange,
    ResetPasswordInput,
    RevokeSessionInput,
    SessionDetailResponse,
    SessionResponse,
    TwoFactorLoginInput,
    TwoFactorSetupResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    VerifyMagicLinkInput,
)
from fastapi_plantilla.modules.auth.utils import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_backup_codes,
    is_safe_callback_url,
    sign_token,
    unsign_token,
)
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.email.service import EmailService
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.service import SystemSettingService

DEFAULT_TOKEN_BYTES: int = 32
DEFAULT_PASSWORD_RESET_EXPIRY_MINUTES: int = 30
DEFAULT_MAGIC_LINK_EXPIRY_MINUTES: int = 15
DEFAULT_EMAIL_VERIFICATION_EXPIRY_HOURS: int = 24
DEFAULT_CREDENTIAL_PROVIDER: str = "credential"
DUMMY_PASSWORD_HASH: str = (
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$RdescudvJCsgqlfreSAeYQ"  # noqa: S105
)

__all__ = [
    "DEFAULT_CREDENTIAL_PROVIDER",
    "DEFAULT_EMAIL_VERIFICATION_EXPIRY_HOURS",
    "DEFAULT_MAGIC_LINK_EXPIRY_MINUTES",
    "DEFAULT_PASSWORD_RESET_EXPIRY_MINUTES",
    "DEFAULT_TOKEN_BYTES",
    "DUMMY_PASSWORD_HASH",
    "AuthService",
    "ph",
]

ph = PasswordHasher()


class AuthService:
    """Service for authentication and session management business logic."""

    def __init__(
        self,
        repository: AuthRepository = Depends(),
        email_service: EmailService = Depends(get_email_service),
        settings_service: SystemSettingService | None = Depends(get_settings_service),
    ) -> None:
        self.repository = repository
        self.email_service = email_service
        self.settings_service = settings_service

    async def get_password_reset_expiry_minutes(self) -> int:
        """Get configured expiry for password reset tokens in minutes."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "auth.password_reset_expiry_minutes",
                    default=DEFAULT_PASSWORD_RESET_EXPIRY_MINUTES,
                )
            )
        return DEFAULT_PASSWORD_RESET_EXPIRY_MINUTES

    async def get_email_verification_expiry_hours(self) -> int:
        """Get configured expiry for email verification tokens in hours."""
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "auth.email_verification_expiry_hours",
                    default=DEFAULT_EMAIL_VERIFICATION_EXPIRY_HOURS,
                )
            )
        return DEFAULT_EMAIL_VERIFICATION_EXPIRY_HOURS

    async def get_magic_link_expiry_minutes(self) -> int:
        """Get configured expiry for magic link tokens in minutes."""
        fallback = getattr(
            settings, "magic_link_expiry_minutes", DEFAULT_MAGIC_LINK_EXPIRY_MINUTES
        )
        if self.settings_service:
            return int(
                await self.settings_service.get_value(
                    "auth.magic_link_expiry_minutes",
                    default=fallback,
                )
            )
        return fallback

    async def _emit_audit(
        self,
        action: str,
        user: User | UserResponse | None = None,
        user_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        actor_name: str | None = None,
        actor_email: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        changes: dict[str, Any] | None = None,
        details: str | None = None,
    ) -> None:
        """Emit decoupled audit event into centralized sys_audit_logs."""
        target_id = user.id if user else user_id
        target_name = (user.name or user.email) if user else (actor_name or actor_email)
        target_email = user.email if user else actor_email
        entry = AuditEntry(
            entity_type="user",
            entity_id=target_id,
            entity_name=target_name,
            action=action,
            actor_id=actor_id if actor_id is not None else target_id,
            actor_name=actor_name if actor_name is not None else target_name,
            actor_email=actor_email if actor_email is not None else target_email,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
            details=details,
        )
        await dispatch_audit_event(self.repository.session, entry)

    async def _create_user_session(
        self,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
        impersonated_by: uuid.UUID | None = None,
    ) -> AuthResponse:
        """Helper to create session token and construct AuthResponse."""
        raw_token = secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)
        session = await self.repository.create_session(
            user_id=user.id,
            token=raw_token,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_expire_days),
            ip_address=ip_address,
            user_agent=user_agent,
            impersonated_by=impersonated_by,
        )
        session_dto = SessionResponse.model_validate(session)
        session_dto.token = sign_token(raw_token, settings.auth_secret)
        return AuthResponse(
            user=UserResponse.model_validate(user),
            session=session_dto,
        )

    async def _create_2fa_challenge(self, user: User) -> AuthResponse:
        """Issue a short-lived 2FA challenge token without creating a session."""
        challenge_token = secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)
        await self.repository.delete_verifications_by_identifier(
            f"2fa_challenge:{user.id}"
        )
        await self.repository.create_verification(
            identifier=f"2fa_challenge:{user.id}",
            value=challenge_token,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        return AuthResponse(
            user=UserResponse.model_validate(user),
            session=None,
            two_factor_required=True,
            two_factor_token=challenge_token,
        )

    async def _handle_oauth_user(
        self,
        user_info: OAuthUserInfo,
        access_token: str | None = None,
        refresh_token: str | None = None,
        id_token: str | None = None,
        expires_at: datetime | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Create session with external OAuth provider, linking or creating user."""
        account = await self.repository.get_user_by_provider_account(
            provider_id=user_info.provider_id, account_id=user_info.account_id
        )

        if account:
            user = await self.repository.get_user_by_id(account.user_id)
            if not user:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Usuario no encontrado",
                )
            if not user.is_active or user.status == RecordStatus.TRASHED:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuario inactivo o suspendido",
                )
            await self.repository.update_account_tokens(
                account=account,
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=id_token,
                expires_at=expires_at,
            )
            if user.two_factor_enabled:
                return await self._create_2fa_challenge(user)

            resp = await self._create_user_session(user, ip_address, user_agent)
            await self._emit_audit(
                action=AuditAction.LOGIN,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"OAuth login via {user_info.provider_id}",
            )
            return resp

        user = await self.repository.get_user_by_email(user_info.email)
        if user:
            if not user.is_active or user.status == RecordStatus.TRASHED:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuario inactivo o suspendido",
                )
            await self.repository.create_account(
                user_id=user.id,
                provider_id=user_info.provider_id,
                account_id=user_info.account_id,
                access_token=access_token,
                refresh_token=refresh_token,
                id_token=id_token,
                access_token_expires_at=expires_at,
            )
            if not user.email_verified and user_info.email_verified:
                await self.repository.update_user_by_id(
                    user_id=user.id, update_data={"email_verified": True}
                )
            if user.two_factor_enabled:
                return await self._create_2fa_challenge(user)

            resp = await self._create_user_session(user, ip_address, user_agent)
            await self._emit_audit(
                action=AuditAction.LOGIN,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"OAuth login and account link via {user_info.provider_id}",
            )
            return resp

        new_user = await self.repository.create_user(
            name=user_info.name,
            email=user_info.email,
            image=user_info.image,
            email_verified=user_info.email_verified,
        )
        await self.repository.create_account(
            user_id=new_user.id,
            provider_id=user_info.provider_id,
            account_id=user_info.account_id,
            access_token=access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            access_token_expires_at=expires_at,
        )
        resp = await self._create_user_session(new_user, ip_address, user_agent)
        await self._emit_audit(
            action=AuditAction.CREATE,
            user=new_user,
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"User registered via {user_info.provider_id} OAuth",
        )
        await self._emit_audit(
            action=AuditAction.LOGIN,
            user=new_user,
            ip_address=ip_address,
            user_agent=user_agent,
            details=f"Initial OAuth login via {user_info.provider_id}",
        )
        return resp

    def get_google_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate Google OAuth authorization URL."""
        return GoogleOAuthClient.get_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
        )

    async def authenticate_google(
        self,
        code: str,
        redirect_uri: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Authenticate user via Google OAuth authorization code."""
        user_info, tokens, expires_at = await GoogleOAuthClient.fetch_user_and_tokens(
            code=code,
            redirect_uri=redirect_uri,
        )
        return await self._handle_oauth_user(
            user_info=user_info,
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            id_token=tokens.get("id_token"),
            expires_at=expires_at,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def register(
        self,
        schema: UserCreate,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Register a new user, create credentials and start active session."""
        if await self.repository.get_user_by_email(schema.email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El email ya está registrado",
            )

        try:
            user = await self.repository.create_user(
                name=schema.name,
                email=schema.email,
                image=None,
            )
            await self.repository.create_account(
                user_id=user.id,
                provider_id=DEFAULT_CREDENTIAL_PROVIDER,
                account_id=schema.email,
                password_hash=ph.hash(schema.password),
            )
        except IntegrityError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El email ya está registrado",
            ) from e

        if settings.frontend_url:
            await self.send_verification_email(email=user.email)
        resp = await self._create_user_session(user, ip_address, user_agent)
        await self._emit_audit(
            action=AuditAction.CREATE,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="User registered via email/password",
        )
        await self._emit_audit(
            action=AuditAction.LOGIN,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="Initial login on registration",
        )
        return resp

    async def login(
        self,
        schema: UserLogin,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Login user and start active session."""
        user = await self.repository.get_user_by_email(schema.email)
        if not user:
            with contextlib.suppress(VerifyMismatchError):
                ph.verify(DUMMY_PASSWORD_HASH, schema.password)
            await self._emit_audit(
                action=AuditAction.LOGIN_FAILED,
                actor_email=schema.email,
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed login attempt for non-existent user '{schema.email}'",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )

        if not user.is_active or user.status == RecordStatus.TRASHED:
            await self._emit_audit(
                action=AuditAction.LOGIN_FAILED,
                user=user,
                actor_email=schema.email,
                ip_address=ip_address,
                user_agent=user_agent,
                details=(
                    f"Failed login attempt for inactive or suspended user "
                    f"'{schema.email}'"
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo o suspendido",
            )

        account = await self.repository.get_account_by_provider(
            user_id=user.id,
            provider_id=DEFAULT_CREDENTIAL_PROVIDER,
        )
        if not account or account.password is None:
            with contextlib.suppress(VerifyMismatchError):
                ph.verify(DUMMY_PASSWORD_HASH, schema.password)
            await self._emit_audit(
                action=AuditAction.LOGIN_FAILED,
                user=user,
                actor_email=schema.email,
                ip_address=ip_address,
                user_agent=user_agent,
                details=(
                    f"Failed login attempt: account without password credentials "
                    f"for '{schema.email}'"
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )

        try:
            ph.verify(account.password, schema.password)
        except VerifyMismatchError as e:
            await self._emit_audit(
                action=AuditAction.LOGIN_FAILED,
                user=user,
                actor_email=schema.email,
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"Failed login attempt: invalid password for '{schema.email}'",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            ) from e

        if user.two_factor_enabled:
            return await self._create_2fa_challenge(user)

        resp = await self._create_user_session(user, ip_address, user_agent)
        await self._emit_audit(
            action=AuditAction.LOGIN,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="User logged in via password",
        )
        return resp

    async def get_session(self, token: str) -> AuthResponse:
        """Validate active session token and return authenticated user data."""
        raw_token = unsign_token(token, settings.auth_secret)
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión no válida",
            )

        session = await self.repository.get_session_with_user(token=raw_token)

        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión no válida o expirada",
            )

        if not session.user.is_active or session.user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo o suspendido",
            )

        session_dto = SessionResponse.model_validate(session)
        session_dto.token = token
        return AuthResponse(
            user=UserResponse.model_validate(session.user),
            session=session_dto,
        )

    async def logout(
        self,
        token: str,
        user: User | UserResponse | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Logout user and invalidate active session."""
        raw_token = unsign_token(token, settings.auth_secret)
        if not raw_token:
            return False
        if not user:
            sess = await self.repository.get_session_with_user(token=raw_token)
            if sess:
                user = sess.user
        result = await self.repository.invalidate_session(token=raw_token)
        if user:
            await self._emit_audit(
                action=AuditAction.LOGOUT,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details="User logged out",
            )
        return result

    async def logout_all(
        self,
        user_id: uuid.UUID | str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> int:
        """Invalidate all active sessions of a user (Logout from all devices)."""
        count = await self.repository.invalidate_all_user_sessions(user_id=user_id)
        user = await self.repository.get_user_by_id(user_id)
        if user:
            await self._emit_audit(
                action=AuditAction.LOGOUT,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details=f"All active sessions revoked ({count} sessions)",
            )
        return count

    async def change_password(
        self,
        user_id: uuid.UUID | str,
        schema: PasswordChange,
        token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Change password, ensure difference and revoke existing sessions."""
        account = await self.repository.get_account_by_provider(
            user_id=user_id,
            provider_id=DEFAULT_CREDENTIAL_PROVIDER,
        )
        if not account or account.password is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Esta cuenta no tiene contraseña configurada",
            )

        try:
            ph.verify(account.password, schema.current_password)
        except VerifyMismatchError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La contraseña actual es incorrecta",
            ) from e

        if schema.current_password == schema.new_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La nueva contraseña debe ser diferente a la actual",
            )

        await self.repository.update_password_hash(
            user_id=user_id,
            new_password_hash=ph.hash(schema.new_password),
        )

        if schema.revoke_other_sessions:
            raw_token = unsign_token(token, settings.auth_secret) or token
            await self.repository.invalidate_other_user_sessions(
                user_id=user_id, current_token=raw_token
            )

        user = await self.repository.get_user_by_id(user_id)
        if user:
            await self._emit_audit(
                action=AuditAction.PASSWORD_CHANGE,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details="Password changed by user",
            )
        return True

    async def _send_reset_password_email(
        self, user: User, token: str, expiry_minutes: int | None = None
    ) -> None:
        """Render and dispatch password reset email."""
        if not settings.frontend_url:
            return

        if expiry_minutes is None:
            expiry_minutes = await self.get_password_reset_expiry_minutes()

        reset_link = str(
            (URL(settings.frontend_url) / "reset-password").with_query(token=token)
        )
        email_msg = (
            self.email_service.create_builder()
            .to(user.email)
            .subject("Restablecer tu contraseña")
            .template(
                "auth/reset_password.html",
                name=user.name,
                reset_link=reset_link,
                expiry_minutes=expiry_minutes,
            )
        )
        try:
            await self.email_service.enqueue_send(
                email_msg,
                session=self.repository.session,
                user_id=user.id,
            )
        except Exception as exc:
            logger.warning(f"Could not enqueue reset password email: {exc}")

    async def _send_verification_email(
        self, user: User, token: str, expiry_hours: int | None = None
    ) -> None:
        """Render and dispatch email verification link."""
        if not settings.frontend_url:
            return

        if expiry_hours is None:
            expiry_hours = await self.get_email_verification_expiry_hours()

        verify_link = str(
            (URL(settings.frontend_url) / "verify-email").with_query(token=token)
        )
        email_msg = (
            self.email_service.create_builder()
            .to(user.email)
            .subject("Verifica tu correo electrónico")
            .template(
                "auth/verify_email.html",
                name=user.name,
                verify_link=verify_link,
                expiry_hours=expiry_hours,
            )
        )
        try:
            await self.email_service.enqueue_send(
                email_msg,
                session=self.repository.session,
                user_id=user.id,
            )
        except Exception as exc:
            logger.warning(f"Could not enqueue verification email: {exc}")

    async def _send_magic_link_email(
        self,
        user: User,
        token: str,
        callback_url: str | None = None,
        expiry_minutes: int | None = None,
    ) -> None:
        """Render and dispatch passwordless magic link email."""
        if not settings.frontend_url:
            return

        if expiry_minutes is None:
            expiry_minutes = await self.get_magic_link_expiry_minutes()

        query_params = {"token": token}
        if callback_url and is_safe_callback_url(callback_url):
            query_params["callback_url"] = callback_url

        magic_link = str(
            (URL(settings.frontend_url) / "magic-link").with_query(query_params)
        )
        email_msg = (
            self.email_service.create_builder()
            .to(user.email)
            .subject("Tu enlace mágico para iniciar sesión")
            .template(
                "auth/magic_link.html",
                name=user.name,
                magic_link=magic_link,
                expiry_minutes=expiry_minutes,
            )
        )
        try:
            await self.email_service.enqueue_send(
                email_msg,
                session=self.repository.session,
                user_id=user.id,
            )
        except Exception as exc:
            logger.warning(f"Could not enqueue magic link email: {exc}")

    async def forget_password(self, schema: ForgotPasswordRequest) -> bool:
        """Generate password reset token (safe against user enumeration)."""
        user = await self.repository.get_user_by_email(schema.email)
        if user and user.is_active and user.status != RecordStatus.TRASHED:
            await self.repository.delete_verifications_by_identifier(schema.email)
            token = secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)
            expiry_minutes = await self.get_password_reset_expiry_minutes()
            await self.repository.create_verification(
                identifier=schema.email,
                value=token,
                expires_at=datetime.now(UTC) + timedelta(minutes=expiry_minutes),
            )
            await self._send_reset_password_email(
                user, token, expiry_minutes=expiry_minutes
            )
        return True

    async def reset_password(
        self,
        schema: ResetPasswordInput,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Reset password using one-time token and revoke sessions."""
        verification = await self.repository.get_valid_verification_by_value(
            value=schema.token,
        )
        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El enlace de recuperación es inválido o ha expirado",
            )

        user = await self.repository.get_user_by_email(verification.identifier)
        if not user or not user.is_active or user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Usuario no encontrado o inactivo",
            )

        updated = await self.repository.update_password_hash(
            user_id=user.id,
            new_password_hash=ph.hash(schema.new_password),
        )
        if not updated:
            await self.repository.create_account(
                user_id=user.id,
                provider_id=DEFAULT_CREDENTIAL_PROVIDER,
                account_id=user.email,
                password_hash=ph.hash(schema.new_password),
            )

        await self.repository.update_user_by_id(
            user_id=user.id,
            update_data={"email_verified": True},
        )

        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )

        await self.repository.invalidate_all_user_sessions(user_id=user.id)
        await self._emit_audit(
            action=AuditAction.PASSWORD_CHANGE,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="Password reset via one-time recovery token",
        )
        return True

    async def send_verification_email(self, email: str) -> bool:
        """Generate verification token and send verification email."""
        user = await self.repository.get_user_by_email(email)
        if (
            user
            and not user.email_verified
            and user.is_active
            and user.status != RecordStatus.TRASHED
        ):
            await self.repository.delete_verifications_by_identifier(user.email)
            token = secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)
            expiry_hours = await self.get_email_verification_expiry_hours()
            await self.repository.create_verification(
                identifier=user.email,
                value=token,
                expires_at=datetime.now(UTC) + timedelta(hours=expiry_hours),
            )
            await self._send_verification_email(user, token, expiry_hours=expiry_hours)

        return True

    async def verify_email(self, token: str) -> bool:
        """Verify user email using a valid verification token."""
        verification = await self.repository.get_valid_verification_by_value(
            value=token,
        )
        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El token de verificación es inválido o ha expirado",
            )

        user = await self.repository.get_user_by_email(verification.identifier)
        if not user or not user.is_active or user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Usuario no encontrado o inactivo",
            )

        await self.repository.update_user_by_id(
            user_id=user.id,
            update_data={"email_verified": True},
        )
        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )
        return True

    async def request_magic_link(
        self,
        schema: MagicLinkRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Generate magic link login token (safe against user enumeration)."""
        user = await self.repository.get_user_by_email(schema.email)
        if user and user.is_active and user.status != RecordStatus.TRASHED:
            await self.repository.delete_verifications_by_identifier(schema.email)
            token = secrets.token_urlsafe(DEFAULT_TOKEN_BYTES)
            expiry_minutes = await self.get_magic_link_expiry_minutes()
            await self.repository.create_verification(
                identifier=schema.email,
                value=token,
                expires_at=datetime.now(UTC) + timedelta(minutes=expiry_minutes),
            )
            await self._send_magic_link_email(
                user=user,
                token=token,
                callback_url=schema.callback_url,
                expiry_minutes=expiry_minutes,
            )
        return True

    async def verify_magic_link(
        self,
        schema: VerifyMagicLinkInput,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Verify magic link token, verify email if needed, and create user session."""
        verification = await self.repository.get_valid_verification_by_value(
            value=schema.token,
        )
        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El enlace de acceso es inválido o ha expirado",
            )

        user = await self.repository.get_user_by_email(verification.identifier)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Usuario no encontrado o inactivo",
            )
        if not user.is_active or user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo o suspendido",
            )

        if not user.email_verified:
            await self.repository.update_user_by_id(
                user_id=user.id,
                update_data={"email_verified": True},
            )
            user.email_verified = True

        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )

        if user.two_factor_enabled:
            return await self._create_2fa_challenge(user)

        resp = await self._create_user_session(user, ip_address, user_agent)
        await self._emit_audit(
            action=AuditAction.LOGIN,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="User logged in via magic link",
        )
        return resp

    async def setup_two_factor(
        self, user: User | UserResponse
    ) -> TwoFactorSetupResponse:
        """Initialize two-factor authentication setup and generate QR code."""
        secret = pyotp.random_base32()
        await self.repository.delete_verifications_by_identifier(f"2fa_setup:{user.id}")
        await self.repository.create_verification(
            identifier=f"2fa_setup:{user.id}",
            value=secret,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        issuer = settings.app_name or "fastapi_plantilla"
        totp = pyotp.TOTP(secret)
        otpauth_url = totp.provisioning_uri(name=user.email, issuer_name=issuer)
        qr = segno.make(otpauth_url, error="m")
        qr_code = qr.svg_data_uri(scale=4)
        return TwoFactorSetupResponse(
            secret=secret,
            otpauth_url=otpauth_url,
            qr_code=qr_code,
        )

    async def enable_two_factor(
        self, user: User | UserResponse, code: str
    ) -> list[str]:
        """Verify initial TOTP code and enable two-factor authentication."""
        verification = await self.repository.get_valid_verification_by_identifier(
            f"2fa_setup:{user.id}"
        )
        if not verification:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No hay una configuración 2FA pendiente o ha expirado",
            )
        secret = verification.value
        totp = pyotp.TOTP(secret)
        if not totp.verify(code.strip(), valid_window=1):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Código de autenticación inválido",
            )

        plain_codes, hashed_codes = generate_backup_codes(count=8)
        encrypted_secret = encrypt_totp_secret(secret, settings.auth_secret)
        await self.repository.update_user_by_id(
            user_id=user.id,
            update_data={
                "two_factor_enabled": True,
                "two_factor_secret": encrypted_secret,
                "two_factor_backup_codes": hashed_codes,
            },
        )
        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )
        await self._emit_audit(
            action=AuditAction.SETTINGS_CHANGE,
            user_id=user.id,
            actor_id=user.id,
            details="Two-factor authentication enabled",
        )
        return plain_codes

    async def disable_two_factor(
        self,
        user: User | UserResponse,
        code: str | None = None,
        password: str | None = None,
    ) -> bool:
        """Disable two-factor authentication confirming TOTP code or password."""
        db_user = await self.repository.get_user_by_id(user.id)
        if not db_user or not db_user.two_factor_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El doble factor de autenticación no está activado",
            )

        verified = False
        if code and db_user.two_factor_secret:
            try:
                decrypted_secret = decrypt_totp_secret(
                    db_user.two_factor_secret, settings.auth_secret
                )
                totp = pyotp.TOTP(decrypted_secret)
                if totp.verify(code.strip(), valid_window=1):
                    verified = True
            except (ValueError, TypeError, Exception) as err:
                logger.debug("2FA TOTP verification failed during disable: {}", err)

        if not verified and password:
            account = await self.repository.get_account_by_provider(
                user_id=user.id, provider_id=DEFAULT_CREDENTIAL_PROVIDER
            )
            if account and account.password:
                try:
                    ph.verify(account.password, password)
                    verified = True
                except VerifyMismatchError:
                    pass

        if not verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Código 2FA o contraseña incorrectos",
            )

        await self.repository.update_user_by_id(
            user_id=user.id,
            update_data={
                "two_factor_enabled": False,
                "two_factor_secret": None,
                "two_factor_backup_codes": None,
            },
        )
        await self._emit_audit(
            action=AuditAction.SETTINGS_CHANGE,
            user=db_user,
            details="Two-factor authentication disabled",
        )
        return True

    async def regenerate_backup_codes(
        self, user: User | UserResponse, code: str
    ) -> list[str]:
        """Regenerate recovery backup codes verifying a valid TOTP code."""
        db_user = await self.repository.get_user_by_id(user.id)
        if (
            not db_user
            or not db_user.two_factor_enabled
            or not db_user.two_factor_secret
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El doble factor de autenticación no está activado",
            )

        decrypted_secret = decrypt_totp_secret(
            db_user.two_factor_secret, settings.auth_secret
        )
        totp = pyotp.TOTP(decrypted_secret)
        if not totp.verify(code.strip(), valid_window=1):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Código de autenticación inválido",
            )

        plain_codes, hashed_codes = generate_backup_codes(count=8)
        await self.repository.update_user_by_id(
            user_id=user.id,
            update_data={"two_factor_backup_codes": hashed_codes},
        )
        await self._emit_audit(
            action=AuditAction.SETTINGS_CHANGE,
            user=db_user,
            details="Two-factor backup recovery codes regenerated",
        )
        return plain_codes

    async def _verify_totp_or_backup_code(
        self, user: User, code: str
    ) -> tuple[bool, bool]:
        """Validate TOTP or backup code, updating remaining codes if backup was used."""
        clean_code = code.strip()
        # 1. Check TOTP
        if user.two_factor_secret:
            try:
                decrypted_secret = decrypt_totp_secret(
                    user.two_factor_secret, settings.auth_secret
                )
                totp = pyotp.TOTP(decrypted_secret)
                if totp.verify(clean_code, valid_window=1):
                    return True, False
            except (ValueError, TypeError, Exception) as err:
                logger.debug("2FA login TOTP verification failed: {}", err)

        # 2. Check Backup Codes
        if user.two_factor_backup_codes:
            code_hash = hashlib.sha256(clean_code.encode("utf-8")).hexdigest()
            for stored_hash in user.two_factor_backup_codes:
                if hmac.compare_digest(stored_hash, code_hash):
                    remaining = [
                        h
                        for h in user.two_factor_backup_codes
                        if not hmac.compare_digest(h, code_hash)
                    ]
                    await self.repository.update_user_by_id(
                        user_id=user.id,
                        update_data={"two_factor_backup_codes": remaining},
                    )
                    return True, True

        return False, False

    async def verify_two_factor_login(
        self,
        schema: TwoFactorLoginInput,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Verify 2FA TOTP or recovery backup code to complete login."""
        verification = await self.repository.get_valid_verification_by_value(
            value=schema.two_factor_token
        )
        if not verification or not verification.identifier.startswith("2fa_challenge:"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El token de desafío es inválido o ha expirado",
            )

        user_id_str = verification.identifier.split(":", 1)[1]
        try:
            user_id = uuid.UUID(user_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Token de desafío corrupto",
            ) from None

        user = await self.repository.get_user_by_id(user_id)
        if not user or not user.is_active or user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo o suspendido",
            )

        if not user.two_factor_enabled or not user.two_factor_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El usuario no tiene 2FA configurado",
            )

        is_valid, used_backup_code = await self._verify_totp_or_backup_code(
            user=user, code=schema.code
        )

        if not is_valid:
            await self._emit_audit(
                action=AuditAction.LOGIN_FAILED,
                user=user,
                ip_address=ip_address,
                user_agent=user_agent,
                details="Failed 2FA verification attempt",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Código de autenticación o código de recuperación inválido",
            )

        # Delete challenge token (single use)
        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )

        audit_detail = (
            "User logged in via 2FA (Recovery Code)"
            if used_backup_code
            else "User logged in via 2FA (TOTP)"
        )
        resp = await self._create_user_session(user, ip_address, user_agent)
        await self._emit_audit(
            action=AuditAction.LOGIN,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details=audit_detail,
        )
        return resp

    async def cleanup_expired_tokens(self) -> dict[str, int]:
        """Delete expired sessions and verification tokens."""
        deleted_sessions = await self.repository.delete_expired_sessions()
        deleted_verifications = await self.repository.delete_expired_verifications()
        return {
            "deleted_sessions": deleted_sessions,
            "deleted_verifications": deleted_verifications,
        }

    async def list_sessions(
        self, user_id: uuid.UUID, current_token: str
    ) -> list[SessionDetailResponse]:
        """Fetch all active user sessions and mark the current active device."""
        raw_token = unsign_token(current_token, settings.auth_secret)
        sessions = await self.repository.get_active_user_sessions(user_id)

        return [
            SessionDetailResponse(
                id=s.id,
                user_agent=s.user_agent,
                ip_address=s.ip_address,
                created_at=s.created_at,
                expires_at=s.expires_at,
                is_current=(s.token == raw_token),
            )
            for s in sessions
        ]

    async def revoke_session(
        self, user_id: uuid.UUID, schema: RevokeSessionInput
    ) -> bool:
        """Invalidate a specific user session by its ID."""
        revoked = await self.repository.invalidate_session_by_id(
            session_id=schema.session_id, user_id=user_id
        )
        if revoked is False:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sesión no encontrada o ya revocada",
            )
        return True

    async def change_email(self, user_id: uuid.UUID, schema: ChangeEmailInput) -> bool:
        """Update user email address and re-trigger verification flow."""
        user = await self.repository.get_user_by_id(user_id)
        if not user or not user.is_active or user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado o inactivo",
            )

        existing_user = await self.repository.get_user_by_email(schema.new_email)
        if existing_user and existing_user.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El email ya está registrado",
            )

        account = await self.repository.get_account_by_provider(
            user_id=user_id, provider_id=DEFAULT_CREDENTIAL_PROVIDER
        )

        if account and account.password:
            if not schema.current_password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Se requiere la contraseña actual",
                )
            try:
                ph.verify(account.password, schema.current_password)
            except VerifyMismatchError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Contraseña actual incorrecta",
                ) from e

            await self.repository.update_account_by_provider(
                user_id=user_id,
                provider_id=DEFAULT_CREDENTIAL_PROVIDER,
                update_data={"account_id": schema.new_email},
            )

        await self.repository.update_user_by_id(
            user_id=user_id,
            update_data={"email": schema.new_email, "email_verified": False},
        )

        if settings.frontend_url:
            await self.send_verification_email(email=schema.new_email)

        return True

    async def delete_user(
        self,
        user_id: uuid.UUID,
        schema: DeleteAccountInput,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> bool:
        """Soft-delete user account, invalidate sessions and credentials."""
        user = await self.repository.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado",
            )

        if user.is_system:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No se puede eliminar un usuario del sistema",
            )

        if user.is_super_admin and user.is_active:
            superadmin_count = await self.repository.count_active_superadmins()
            if superadmin_count <= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        "No se puede eliminar el único superadministrador activo "
                        "del sistema"
                    ),
                )

        account = await self.repository.get_account_by_provider(
            user_id=user_id, provider_id=DEFAULT_CREDENTIAL_PROVIDER
        )
        if account and account.password:
            if not schema.password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Se requiere la contraseña para eliminar la cuenta",
                )
            try:
                ph.verify(account.password, schema.password)
            except VerifyMismatchError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Contraseña incorrecta",
                ) from e

        now = datetime.now(UTC)
        await self.repository.update_user_by_id(
            user_id=user_id,
            update_data={
                "status": RecordStatus.TRASHED,
                "is_active": False,
                "deleted_at": now,
                "deleted_by": user.email,
            },
        )
        await self.repository.invalidate_all_user_sessions(user_id=user.id)
        await dispatch_trash_hook(
            self.repository.session,
            user,
            is_trash=True,
            user_id=user.id,
        )
        await self._emit_audit(
            action=AuditAction.TRASH,
            user=user,
            ip_address=ip_address,
            user_agent=user_agent,
            details="User self-deleted account (moved to trash)",
        )
        return True

    async def impersonate_user(
        self,
        admin_user: UserResponse,
        target_user_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Create an impersonated session allowing SuperAdmin to act as target user."""
        if not admin_user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo los superadministradores pueden iniciar impersonación",
            )
        if admin_user.id == target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No puedes impersonarte a ti mismo",
            )

        target_user = await self.repository.get_user_by_id(target_user_id)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario objetivo no encontrado",
            )
        if target_user.is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se permite impersonar a otro superadministrador",
            )
        if not target_user.is_active or target_user.status == RecordStatus.TRASHED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No se puede impersonar a un usuario inactivo o suspendido",
            )

        resp = await self._create_user_session(
            user=target_user,
            ip_address=ip_address,
            user_agent=user_agent,
            impersonated_by=admin_user.id,
        )
        await self._emit_audit(
            action=AuditAction.IMPERSONATE,
            user=admin_user,
            ip_address=ip_address,
            user_agent=user_agent,
            details=(
                f"SuperAdmin {admin_user.email} started impersonating "
                f"{target_user.email} (ID: {target_user.id})"
            ),
        )
        return resp

    async def exit_impersonation(
        self,
        token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse | None:
        """Terminate the active impersonated session and restore admin session."""
        raw_token = unsign_token(token, settings.auth_secret)
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de sesión inválido",
            )
        session = await self.repository.get_session_with_user(token=raw_token)
        if not session or not session.impersonated_by:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La sesión actual no es una sesión impersonada",
            )
        target_user = session.user
        admin_user = await self.repository.get_user_by_id(session.impersonated_by)
        await self.repository.invalidate_session(token=raw_token)

        auth_data: AuthResponse | None = None
        if admin_user and admin_user.is_active:
            auth_data = await self._create_user_session(
                user=admin_user,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        if admin_user:
            await self._emit_audit(
                action=AuditAction.IMPERSONATE,
                user=admin_user,
                ip_address=ip_address,
                user_agent=user_agent,
                details=(
                    f"SuperAdmin {admin_user.email} exited impersonation of "
                    f"{target_user.email} (ID: {target_user.id})"
                ),
            )
        return auth_data
