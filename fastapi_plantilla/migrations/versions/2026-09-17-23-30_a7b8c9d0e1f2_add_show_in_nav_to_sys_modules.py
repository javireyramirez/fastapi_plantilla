"""add_show_in_nav_to_sys_modules.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-17 23:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.add_column(
        "sys_modules",
        sa.Column(
            "show_in_nav",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("sys_modules", "show_in_nav")
