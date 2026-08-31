from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException, status
from yarl import URL

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.auth.schema import OAuthUserInfo


class GoogleOAuthClient:
    """Google OAuth 2.0 / OpenID Connect client."""

    AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105
    USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

    @classmethod
    def get_authorization_url(cls, redirect_uri: str, state: str) -> str:
        """Generate Google OAuth authorization URL with CSRF state."""
        if not settings.google_client_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth no está configurado en el servidor",
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
        return str(URL(cls.AUTH_ENDPOINT).with_query(query_params))

    @classmethod
    async def fetch_user_and_tokens(
        cls,
        code: str,
        redirect_uri: str,
    ) -> tuple[OAuthUserInfo, dict[str, Any], datetime | None]:
        """Exchange authorization code with Google for tokens and fetch user profile."""
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth no está configurado en el servidor",
            )

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                cls.TOKEN_ENDPOINT,
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
                cls.USERINFO_ENDPOINT,
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

        return user_info, tokens, expires_at
