"""add_is_trasheable_to_sys_modules.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-10 23:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add is_trasheable column to sys_modules."""
    op.add_column(
        "sys_modules",
        sa.Column(
            "is_trasheable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop is_trasheable column from sys_modules."""
    op.drop_column("sys_modules", "is_trasheable")
