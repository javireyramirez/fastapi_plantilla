import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    model_validator,
)

MIN_PASSWORD_LENGTH: int = 8


def validate_password_rules(v: str) -> str:
    """Password validation function."""

    v = v.strip()
    if len(v) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
        )
    if not re.search(r"[a-z]", v):
        raise ValueError("Debe incluir al menos una letra minúscula")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Debe incluir al menos una letra mayúscula")
    if not re.search(r"\d", v):
        raise ValueError("Debe incluir al menos un número")
    if not re.search(r"[^A-Za-z0-9]", v):
        raise ValueError("Debe incluir al menos un símbolo (p.ej. !@#$%^&*)")
    if " " in v:
        raise ValueError("No debe contener espacios")
    return v


PasswordStr = Annotated[str, AfterValidator(validate_password_rules)]


class UserBase(BaseModel):
    """User Base Schema."""

    name: str
    email: EmailStr


class UserCreate(UserBase):
    """User Create Schema."""

    password: PasswordStr


class UserUpdate(BaseModel):
    """User Update Schema."""

    name: str | None = None
    email: EmailStr | None = None


class PasswordChange(BaseModel):
    """Password Change Schema."""

    current_password: str
    new_password: PasswordStr
    revoke_other_sessions: bool = True


class ForgotPasswordRequest(BaseModel):
    """Forgot password Schema."""

    email: EmailStr


class SendVerificationEmailRequest(BaseModel):
    """Send verification email Schema."""

    email: EmailStr


class ResetPasswordInput(BaseModel):
    """Reset password Schema."""

    token: str
    new_password: PasswordStr


class UserLogin(BaseModel):
    """User Login Schema."""

    email: EmailStr
    password: str


class VerifyEmailInput(BaseModel):
    """Verify Email Schema."""

    token: str


class UserResponse(UserBase):
    """User Response Schema."""

    id: uuid.UUID
    email_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    is_system: bool
    is_super_admin: bool
    two_factor_enabled: bool = False

    model_config = ConfigDict(from_attributes=True)


class SessionResponse(BaseModel):
    """Session Response Schema."""

    id: uuid.UUID
    user_id: uuid.UUID
    token: str
    expires_at: datetime
    ip_address: str | None = None
    user_agent: str | None = None
    is_valid: bool
    impersonated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    """Auth Response Schema."""

    user: UserResponse
    session: SessionResponse | None = None
    two_factor_required: bool = False
    two_factor_token: str | None = None

    model_config = ConfigDict(from_attributes=True)


class OAuthUserInfo(BaseModel):
    """Normalized user profile from OAuth / OIDC providers."""

    provider_id: str
    account_id: str
    email: EmailStr
    name: str
    image: str | None = None
    email_verified: bool = True


class SessionDetailResponse(BaseModel):
    """Active session detail for device management."""

    id: uuid.UUID
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime
    expires_at: datetime
    is_current: bool = False
    impersonated_by: uuid.UUID | None = None

    model_config = ConfigDict(from_attributes=True)


class RevokeSessionInput(BaseModel):
    """Schema to revoke a specific active session."""

    session_id: uuid.UUID


class ChangeEmailInput(BaseModel):
    """Schema to update the user email address."""

    new_email: EmailStr
    current_password: str | None = None


class DeleteAccountInput(BaseModel):
    """Schema to confirm account deletion."""

    password: str | None = None


class MagicLinkRequest(BaseModel):
    """Payload to request a passwordless magic login link."""

    email: EmailStr
    callback_url: str | None = None


class VerifyMagicLinkInput(BaseModel):
    """Payload to verify magic link token and establish session."""

    token: str


class TwoFactorSetupResponse(BaseModel):
    """Response returned when initiating 2FA setup."""

    secret: str
    otpauth_url: str
    qr_code: str


class TwoFactorEnableInput(BaseModel):
    """Payload to verify TOTP code and finalize 2FA enablement."""

    code: str = Field(..., min_length=6, max_length=6)


class TwoFactorEnableResponse(BaseModel):
    """Response returned upon successfully enabling 2FA with emergency backup codes."""

    backup_codes: list[str]


class TwoFactorDisableInput(BaseModel):
    """Payload to disable 2FA.

    Requires either a valid TOTP code or current password.
    """

    code: str | None = None
    password: str | None = None

    @model_validator(mode="after")
    def check_at_least_one(self) -> "TwoFactorDisableInput":
        """Verify that at least code or password is provided."""
        if not self.code and not self.password:
            raise ValueError("Debe proporcionar un código 2FA o su contraseña actual")
        return self


class TwoFactorLoginInput(BaseModel):
    """Payload to complete two-factor authentication login."""

    two_factor_token: str
    code: str


class TwoFactorRecoveryCodesResponse(BaseModel):
    """Response returned when regenerating recovery backup codes."""

    backup_codes: list[str]
