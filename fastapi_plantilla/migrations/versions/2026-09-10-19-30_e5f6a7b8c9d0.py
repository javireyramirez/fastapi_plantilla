"""add_target_entity_columns_to_sys_trash_bin.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-10 19:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add target_entity columns to sys_trash_bin."""
    op.add_column(
        "sys_trash_bin",
        sa.Column("target_entity_type", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "sys_trash_bin",
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "sys_trash_bin",
        sa.Column("target_entity_name", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_sys_trash_bin_target_entity",
        "sys_trash_bin",
        ["target_entity_type", "target_entity_id"],
    )


def downgrade() -> None:
    """Drop target_entity columns from sys_trash_bin."""
    op.drop_index("ix_sys_trash_bin_target_entity", table_name="sys_trash_bin")
    op.drop_column("sys_trash_bin", "target_entity_name")
    op.drop_column("sys_trash_bin", "target_entity_id")
    op.drop_column("sys_trash_bin", "target_entity_type")
