import secrets
import uuid

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
from fastapi_plantilla.core.crud.schema import MessageResponse
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_session,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import (
    AuthResponse,
    ChangeEmailInput,
    DeleteAccountInput,
    ForgotPasswordRequest,
    PasswordChange,
    ResetPasswordInput,
    RevokeSessionInput,
    SendVerificationEmailRequest,
    SessionDetailResponse,
    UserCreate,
    UserLogin,
    UserResponse,
    VerifyEmailInput,
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


@router.get("/list-sessions", response_model=list[SessionDetailResponse])
async def list_sessions(
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> list[SessionDetailResponse]:
    """
    List all active sessions for the authenticated user.

    Identifies the current active device and returns metadata for each session.
    """
    if not session.session:
        return []
    return await service.list_sessions(
        user_id=session.user.id,
        current_token=session.session.token,
    )


@router.post("/revoke-session", response_model=bool)
async def revoke_session(
    schema: RevokeSessionInput,
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> bool:
    """
    Revoke a specific active session by its ID.

    Terminates the specified device session for the authenticated user.
    """
    return await service.revoke_session(
        user_id=session.user.id,
        schema=schema,
    )


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


@router.post("/change-email", response_model=bool)
async def change_email(
    schema: ChangeEmailInput,
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> bool:
    """
    Change the email address for the authenticated user.

    Updates the user's email and re-triggers email verification if configured.
    """
    return await service.change_email(
        user_id=session.user.id,
        schema=schema,
    )


@router.post("/delete-user", response_model=bool)
async def delete_user(
    response: Response,
    schema: DeleteAccountInput,
    service: AuthService = Depends(),
    session: AuthResponse = Depends(get_current_session),
) -> bool:
    """
    Permanently delete the authenticated user's account.

    Deletes the user, cascade removes sessions and accounts,
    and clears the session cookie.
    """
    await service.delete_user(
        user_id=session.user.id,
        schema=schema,
    )
    delete_session_cookie(response=response)
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


@router.post("/send-verification-email", response_model=bool)
async def send_verification_email(
    schema: SendVerificationEmailRequest,
    service: AuthService = Depends(),
) -> bool:
    """
    Send an email verification link.

    Generates a single-use verification token and sends the verification email.
    """
    return await service.send_verification_email(email=schema.email)


@router.post("/verify-email", response_model=bool)
async def verify_email(
    schema: VerifyEmailInput,
    service: AuthService = Depends(),
) -> bool:
    """
    Verify user email with a verification token.

    Marks the user's email as verified and consumes the one-time token.
    """
    return await service.verify_email(token=schema.token)


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


@router.post("/impersonate/exit", response_model=MessageResponse)
async def exit_impersonation(
    response: Response,
    current_session: AuthResponse = Depends(get_current_session),
    service: AuthService = Depends(),
) -> MessageResponse:
    """Exit impersonated session and revoke it."""
    if not current_session.session or not current_session.session.token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active session found",
        )
    await service.exit_impersonation(current_session.session.token)
    delete_session_cookie(response)
    return MessageResponse(message="Impersonación finalizada")


@router.post("/impersonate/{user_id}", response_model=AuthResponse)
async def impersonate_user(
    user_id: uuid.UUID,
    request: Request,
    response: Response,
    admin: UserResponse = Depends(get_current_active_superuser),
    service: AuthService = Depends(),
) -> AuthResponse:
    """Start an impersonated session as target user (SuperAdmin only)."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    auth_data = await service.impersonate_user(
        admin_user=admin,
        target_user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if auth_data.session:
        set_session_cookie(response, auth_data.session.token)
    return auth_data
