import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import (
    AuditFieldsMixin,
    OptimisticLockMixin,
    TimestampMixin,
    UUID7PrimaryKeyMixin,
)


class User(UUID7PrimaryKeyMixin, AuditFieldsMixin, OptimisticLockMixin, Base):
    """User model."""

    __tablename__ = "auth_users"

    name: Mapped[str] = mapped_column(String(length=200))
    email: Mapped[str] = mapped_column(String(length=200), unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    image: Mapped[str | None] = mapped_column(String(length=500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relaciones
    accounts: Mapped[list["Account"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["Session"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Session.user_id",
    )


class Account(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Account model for OAuth & Credentials providers."""

    __tablename__ = "auth_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_id: Mapped[str] = mapped_column(String(length=100))
    account_id: Mapped[str] = mapped_column(String(length=255))
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    id_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scope: Mapped[str | None] = mapped_column(String(length=500), nullable=True)
    password: Mapped[str | None] = mapped_column(String(length=255), nullable=True)

    # Relación inversa
    user: Mapped["User"] = relationship(back_populates="accounts")

    __table_args__ = (
        UniqueConstraint("provider_id", "account_id", name="uq_provider_account"),
    )


class Session(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Session model."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token: Mapped[str] = mapped_column(
        String(length=255), unique=True, index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(length=45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    impersonated_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        default=None,
    )

    # Relación inversa
    user: Mapped["User"] = relationship(
        back_populates="sessions", foreign_keys=[user_id]
    )


class Verification(UUID7PrimaryKeyMixin, TimestampMixin, Base):
    """Verification model for magic links, OTPs and email confirmations."""

    __tablename__ = "auth_verifications"

    identifier: Mapped[str] = mapped_column(String(length=255), nullable=False)
    value: Mapped[str] = mapped_column(String(length=255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ix_auth_verifications_identifier_value", "identifier", "value"),
    )
