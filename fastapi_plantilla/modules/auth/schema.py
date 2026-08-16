import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr


def validate_password_rules(v: str) -> str:
    """Password validation function."""

    v = v.strip()
    if len(v) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
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
    image: str | None = None


class UserCreate(UserBase):
    """User Create Schema."""

    password: PasswordStr


class UserUpdate(BaseModel):
    """User Update Schema."""

    name: str | None = None
    email: EmailStr | None = None
    image: str | None = None


class PasswordChange(BaseModel):
    """Password Change Schema."""

    current_password: str
    new_password: PasswordStr
    revoke_other_sessions: bool = False


class ForgotPasswordRequest(BaseModel):
    """Forgot password Schema."""

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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    """Auth Response Schema."""

    user: UserResponse
    session: SessionResponse | None = None

    model_config = ConfigDict(from_attributes=True)


class OAuthUserInfo(BaseModel):
    """Normalized user profile from OAuth / OIDC providers."""

    provider_id: str
    account_id: str
    email: EmailStr
    name: str
    image: str | None = None
    email_verified: bool = True
