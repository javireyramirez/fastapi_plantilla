import secrets

from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_session,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    ForgotPasswordRequest,
    PasswordChange,
    ResetPasswordInput,
    UserCreate,
    UserLogin,
    UserResponse,
)
from fastapi_plantilla.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


def set_session_cookie(response: Response, token: str) -> None:
    """Set HttpOnly session cookie."""
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_expire_days * 86400,
        path="/",
    )


def delete_session_cookie(response: Response) -> None:
    """Delete session cookie."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
    )


@router.post("/sign-up/email", response_model=AuthResponse)
async def sign_up_email(
    user: UserCreate,
    request: Request,
    response: Response,
    service: AuthService = Depends(),
) -> AuthResponse:
    """
    Register a new user with email and password.

    Creates the user account, starts an active session, and sets the HttpOnly cookie.
    """
    result = await service.register(
        schema=user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if result.session:
        set_session_cookie(response, result.session.token)

    return result


@router.post("/sign-in/email", response_model=AuthResponse)
async def sign_in_email(
    user: UserLogin,
    request: Request,
    response: Response,
    service: AuthService = Depends(),
) -> AuthResponse:
    """
    Authenticate user with email and password.

    Validates credentials, creates a new session, and sets the HttpOnly cookie.
    """
    result = await service.login(
        schema=user,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if result.session:
        set_session_cookie(response, result.session.token)

    return result


@router.post("/sign-out", response_model=bool)
async def sign_out(
    response: Response,
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> bool:
    """
    Log out the current user.

    Invalidates the active session token in database and deletes the session cookie.
    """
    if session.session:
        await service.logout(token=session.session.token)
    delete_session_cookie(response=response)
    return True


@router.get("/get-session", response_model=AuthResponse)
async def get_session(
    session: AuthResponse = Depends(get_current_session),
) -> AuthResponse:
    """
    Get current active session and user data.

    Returns the authenticated user profile and session metadata.
    """
    return session


@router.post("/revoke-sessions", response_model=bool)
async def revoke_sessions(
    response: Response,
    service: AuthService = Depends(),
    user: UserResponse = Depends(get_current_user),
) -> bool:
    """
    Revoke all active sessions across all devices.

    Logs out the user from all connections and clears the local session cookie.
    """
    if user:
        await service.logout_all(user_id=user.id)
    delete_session_cookie(response=response)
    return True


@router.post("/change-password", response_model=bool)
async def change_password(
    schema: PasswordChange,
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> bool:
    """
    Change password for authenticated user.

    Optionally revokes other active sessions if revoke_other_sessions is true.
    """
    if session.session:
        await service.change_password(
            user_id=session.user.id,
            schema=schema,
            token=session.session.token,
        )
    return True


@router.post("/forget-password", response_model=bool)
async def forget_password(
    schema: ForgotPasswordRequest,
    service: AuthService = Depends(),
) -> bool:
    """
    Request a password reset link.

    Generates a single-use verification token without revealing account existence.
    """
    await service.forget_password(schema=schema)
    return True


@router.post("/reset-password", response_model=bool)
async def reset_password(
    schema: ResetPasswordInput,
    service: AuthService = Depends(),
) -> bool:
    """
    Reset user password using a verification token.

    Validates the one-time token, updates password hash, and revokes previous sessions.
    """
    await service.reset_password(schema=schema)
    return True


@router.get("/sign-in/social/google", response_class=RedirectResponse)
async def sign_in_google(
    request: Request,
    callback_url: str | None = None,
    service: AuthService = Depends(),
) -> RedirectResponse:
    """Redirect to Google OAuth consent screen with CSRF protection."""
    state = secrets.token_urlsafe(32)
    redirect_uri = str(request.url_for("google_callback"))
    auth_url = service.get_google_auth_url(redirect_uri, state)
    redirect = RedirectResponse(
        url=auth_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT
    )
    redirect.set_cookie(
        key="oauth_state",
        value=state,
        max_age=5 * 60,
        httponly=True,
        samesite="lax",
    )
    if callback_url:
        redirect.set_cookie(
            key="oauth_callback_url",
            value=callback_url,
            max_age=5 * 60,
            httponly=True,
            samesite="lax",
        )
    return redirect


@router.get(
    "/callback/google",
    name="google_callback",
    response_model=None,
)
async def callback_google(
    request: Request,
    response: Response,
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
    oauth_callback_url: str | None = Cookie(default=None),
    service: AuthService = Depends(),
) -> AuthResponse | RedirectResponse:
    """Process Google OAuth callback, authenticate user, and issue session."""
    if not oauth_state or oauth_state != state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Estado de autenticación inválido o expirado",
        )
    redirect_uri = str(request.url_for("google_callback"))
    auth_data = await service.authenticate_google(
        code=code,
        redirect_uri=redirect_uri,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    if oauth_callback_url:
        redirect = RedirectResponse(
            url=oauth_callback_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        if auth_data.session:
            set_session_cookie(redirect, auth_data.session.token)
        redirect.delete_cookie("oauth_state", path="/")
        redirect.delete_cookie("oauth_callback_url", path="/")
        return redirect

    if auth_data.session:
        set_session_cookie(response, auth_data.session.token)
    response.delete_cookie("oauth_state", path="/")
    response.delete_cookie("oauth_callback_url", path="/")
    return auth_data
