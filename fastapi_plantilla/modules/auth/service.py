import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, status
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.auth.repository import AuthRepository
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    ForgotPasswordRequest,
    OAuthUserInfo,
    PasswordChange,
    ResetPasswordInput,
    SessionResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from fastapi_plantilla.modules.auth.utils import sign_token, unsign_token

ph = PasswordHasher()


class AuthService:
    """Service for authentication and session management business logic."""

    def __init__(self, repository: AuthRepository = Depends()) -> None:
        self.repository = repository

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
        if not settings.google_client_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth no está configurado",
            )

        query_params = {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return str(
            URL("https://accounts.google.com/o/oauth2/v2/auth").with_query(
                query_params,
            ),
        )

    async def authenticate_google(
        self,
        code: str,
        redirect_uri: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthResponse:
        """Authenticate user via Google OAuth authorization code."""
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth no está configurado en el servidor",
            )

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )
            if token_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Error al validar la autorización con Google",
                )
            tokens = token_response.json()

            userinfo_response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if userinfo_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Error al obtener el perfil de usuario de Google",
                )
            profile = userinfo_response.json()

        user_info = OAuthUserInfo(
            provider_id="google",
            account_id=profile["sub"],
            email=profile["email"],
            name=profile.get("name", profile["email"]),
            image=profile.get("picture"),
            email_verified=profile.get("email_verified", True),
        )

        expires_at = (
            datetime.now(UTC) + timedelta(seconds=tokens["expires_in"])
            if "expires_in" in tokens
            else None
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

    async def forget_password(self, schema: ForgotPasswordRequest) -> bool:
        """Generate password reset token (safe against user enumeration)."""
        user = await self.repository.get_user_by_email(schema.email)
        if user and user.is_active:
            await self.repository.create_verification(
                identifier=schema.email,
                value=secrets.token_urlsafe(32),
                expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
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

    async def cleanup_expired_tokens(self) -> dict[str, int]:
        """Delete expired sessions and verification tokens."""
        deleted_sessions = await self.repository.delete_expired_sessions()
        deleted_verifications = await self.repository.delete_expired_verifications()
        return {
            "deleted_sessions": deleted_sessions,
            "deleted_verifications": deleted_verifications,
        }
