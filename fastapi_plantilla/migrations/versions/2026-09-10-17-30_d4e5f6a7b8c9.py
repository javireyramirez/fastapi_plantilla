"""add_entity_name_to_sys_audit_logs.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-10 17:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add entity_name column to sys_audit_logs."""
    op.add_column(
        "sys_audit_logs",
        sa.Column("entity_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Drop entity_name column from sys_audit_logs."""
    op.drop_column("sys_audit_logs", "entity_name")
