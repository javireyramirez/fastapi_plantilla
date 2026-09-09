"""create_sys_audit_logs_table.

Revision ID: a1b2c3d4e5f6
Revises: e93c56b3f205
Create Date: 2026-09-09 11:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1b2c3d4e5f6"
down_revision = "e93c56b3f205"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create sys_audit_logs table."""
    op.create_table(
        "sys_audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("actor_email", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "changes",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sys_audit_logs"),
    )
    op.create_index(
        "ix_sys_audit_logs_entity_type",
        "sys_audit_logs",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        "ix_sys_audit_logs_entity_id",
        "sys_audit_logs",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_audit_logs_action",
        "sys_audit_logs",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_sys_audit_logs_actor_id",
        "sys_audit_logs",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_audit_logs_created_at",
        "sys_audit_logs",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop sys_audit_logs table."""
    op.drop_index("ix_sys_audit_logs_created_at", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_actor_id", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_action", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_entity_id", table_name="sys_audit_logs")
    op.drop_index("ix_sys_audit_logs_entity_type", table_name="sys_audit_logs")
    op.drop_table("sys_audit_logs")
