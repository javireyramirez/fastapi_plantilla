import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.oauth import GoogleOAuthClient
from fastapi_plantilla.modules.auth.repository import AuthRepository
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    ChangeEmailInput,
    DeleteAccountInput,
    ForgotPasswordRequest,
    OAuthUserInfo,
    PasswordChange,
    ResetPasswordInput,
    RevokeSessionInput,
    SessionDetailResponse,
    SessionResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from fastapi_plantilla.modules.auth.utils import sign_token, unsign_token
from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.email.service import EmailService

ph = PasswordHasher()


class AuthService:
    """Service for authentication and session management business logic."""

    def __init__(
        self,
        repository: AuthRepository = Depends(),
        email_service: EmailService = Depends(get_email_service),
    ) -> None:
        self.repository = repository
        self.email_service = email_service

    async def _create_user_session(
        self,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Helper to create session token and construct AuthResponse."""
        raw_token = secrets.token_urlsafe(32)
        session = await self.repository.create_session(
            user_id=user.id,
            token=raw_token,
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_expire_days),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session_dto = SessionResponse.model_validate(session)
        session_dto.token = sign_token(raw_token, settings.auth_secret)
        return AuthResponse(
            user=UserResponse.model_validate(user),
            session=session_dto,
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
            if not user.is_active:
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
            return await self._create_user_session(user, ip_address, user_agent)

        user = await self.repository.get_user_by_email(user_info.email)
        if user:
            if not user.is_active:
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
            if not user.email_verified:
                await self.repository.update_user_by_id(
                    user_id=user.id, update_data={"email_verified": True}
                )
            return await self._create_user_session(user, ip_address, user_agent)

        new_user = await self.repository.create_user(
            name=user_info.name,
            email=user_info.email,
            image=user_info.image,
            email_verified=True,
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
        return await self._create_user_session(new_user, ip_address, user_agent)

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

        user = await self.repository.create_user(
            name=schema.name,
            email=schema.email,
            image=schema.image,
        )
        await self.repository.create_account(
            user_id=user.id,
            provider_id="credential",
            account_id=schema.email,
            password_hash=ph.hash(schema.password),
        )
        if settings.frontend_url:
            await self.send_verification_email(email=user.email)
        return await self._create_user_session(user, ip_address, user_agent)

    async def login(
        self,
        schema: UserLogin,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Login user and start active session."""
        user = await self.repository.get_user_by_email(schema.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo o suspendido",
            )

        account = await self.repository.get_account_by_provider(
            user_id=user.id,
            provider_id="credential",
        )
        if not account or account.password is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )

        try:
            ph.verify(account.password, schema.password)
        except VerifyMismatchError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            ) from e

        return await self._create_user_session(user, ip_address, user_agent)

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

        if not session.user.is_active:
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

    async def logout(self, token: str) -> bool:
        """Logout user and invalidate active session."""
        raw_token = unsign_token(token, settings.auth_secret)
        if not raw_token:
            return False
        return await self.repository.invalidate_session(token=raw_token)

    async def logout_all(self, user_id: uuid.UUID | str) -> int:
        """Invalidate all active sessions of a user (Logout from all devices)."""
        return await self.repository.invalidate_all_user_sessions(user_id=user_id)

    async def change_password(
        self,
        user_id: uuid.UUID | str,
        schema: PasswordChange,
        token: str,
    ) -> bool:
        """Change password, ensure difference and revoke existing sessions."""
        account = await self.repository.get_account_by_provider(
            user_id=user_id,
            provider_id="credential",
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
        return True

    async def _send_reset_password_email(self, user: User, token: str) -> None:
        """Render and dispatch password reset email."""
        if not settings.frontend_url:
            return

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
            )
        )
        await self.email_service.send(email_msg)

    async def _send_verification_email(self, user: User, token: str) -> None:
        """Render and dispatch email verification link."""
        if not settings.frontend_url:
            return

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
            )
        )
        await self.email_service.send(email_msg)

    async def forget_password(self, schema: ForgotPasswordRequest) -> bool:
        """Generate password reset token (safe against user enumeration)."""
        user = await self.repository.get_user_by_email(schema.email)
        if user and user.is_active:
            token = secrets.token_urlsafe(32)
            await self.repository.create_verification(
                identifier=schema.email,
                value=token,
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            await self._send_reset_password_email(user, token)
        return True

    async def reset_password(self, schema: ResetPasswordInput) -> bool:
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
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Usuario no encontrado o inactivo",
            )

        await self.repository.update_password_hash(
            user_id=user.id,
            new_password_hash=ph.hash(schema.new_password),
        )

        await self.repository.delete_verification(
            identifier=verification.identifier,
            value=verification.value,
        )

        await self.repository.invalidate_all_user_sessions(user_id=user.id)
        return True

    async def send_verification_email(self, email: str) -> bool:
        """Generate verification token and send verification email."""
        user = await self.repository.get_user_by_email(email)
        if user and not user.email_verified and user.is_active:
            token = secrets.token_urlsafe(32)
            await self.repository.create_verification(
                identifier=user.email,
                value=token,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
            await self._send_verification_email(user, token)

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
        if not user or not user.is_active:
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
        if not user or not user.is_active:
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
            user_id=user_id, provider_id="credential"
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
                provider_id="credential",
                update_data={"account_id": schema.new_email},
            )

        await self.repository.update_user_by_id(
            user_id=user_id,
            update_data={"email": schema.new_email, "email_verified": False},
        )

        if settings.frontend_url:
            await self.send_verification_email(email=schema.new_email)

        return True

    async def delete_user(self, user_id: uuid.UUID, schema: DeleteAccountInput) -> bool:
        """Permanently delete user account and invalidate credentials."""
        user = await self.repository.get_user_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado",
            )

        account = await self.repository.get_account_by_provider(
            user_id=user_id, provider_id="credential"
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

        await self.repository.delete_user_by_id(user_id=user_id)
        return True
