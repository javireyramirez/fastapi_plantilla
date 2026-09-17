"""create_sys_notifications.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-17 16:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "sys_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=150), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "INFO",
                "SUCCESS",
                "WARNING",
                "ERROR",
                "JOB_COMPLETED",
                "JOB_FAILED",
                "SYSTEM",
                name="notificationtype",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("action_url", sa.String(length=255), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["auth_users.id"],
            name="fk_sys_notifications_recipient_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sys_notifications"),
    )
    op.create_index(
        "idx_sys_notifications_recipient_id",
        "sys_notifications",
        ["recipient_id"],
        unique=False,
    )
    op.create_index(
        "idx_sys_notifications_type",
        "sys_notifications",
        ["type"],
        unique=False,
    )
    op.create_index(
        "idx_sys_notifications_read_at",
        "sys_notifications",
        ["read_at"],
        unique=False,
    )
    op.create_index(
        "idx_sys_notifications_recipient_read",
        "sys_notifications",
        ["recipient_id", "read_at"],
        unique=False,
    )
    op.create_index(
        "idx_sys_notifications_recipient_created",
        "sys_notifications",
        ["recipient_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_sys_notifications_entity",
        "sys_notifications",
        ["entity_type", "entity_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        "idx_sys_notifications_entity", table_name="sys_notifications"
    )
    op.drop_index(
        "idx_sys_notifications_recipient_created",
        table_name="sys_notifications",
    )
    op.drop_index(
        "idx_sys_notifications_recipient_read", table_name="sys_notifications"
    )
    op.drop_index(
        "idx_sys_notifications_read_at", table_name="sys_notifications"
    )
    op.drop_index("idx_sys_notifications_type", table_name="sys_notifications")
    op.drop_index(
        "idx_sys_notifications_recipient_id", table_name="sys_notifications"
    )
    op.drop_table("sys_notifications")
