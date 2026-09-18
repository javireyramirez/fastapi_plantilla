"""create_auth_api_keys_and_sys_email_logs.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-09-19 01:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    # 1. Create auth_api_keys
    op.create_table(
        "auth_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("masked_key", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["auth_users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_api_keys_key_hash"), "auth_api_keys", ["key_hash"], unique=True)
    op.create_index(op.f("ix_auth_api_keys_owner_id"), "auth_api_keys", ["owner_id"], unique=False)
    op.create_index(op.f("ix_auth_api_keys_is_active"), "auth_api_keys", ["is_active"], unique=False)

    # 2. Create sys_email_logs
    op.create_table(
        "sys_email_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("to", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("template_name", sa.String(length=150), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="PENDING"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["sys_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["auth_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_sys_email_logs_job_id"), "sys_email_logs", ["job_id"], unique=False)
    op.create_index(op.f("ix_sys_email_logs_status"), "sys_email_logs", ["status"], unique=False)
    op.create_index(op.f("ix_sys_email_logs_template_name"), "sys_email_logs", ["template_name"], unique=False)
    op.create_index(op.f("ix_sys_email_logs_user_id"), "sys_email_logs", ["user_id"], unique=False)
    op.create_index("ix_sys_email_logs_job_attempt", "sys_email_logs", ["job_id", "attempt"], unique=False)


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("sys_email_logs")
    op.drop_table("auth_api_keys")
