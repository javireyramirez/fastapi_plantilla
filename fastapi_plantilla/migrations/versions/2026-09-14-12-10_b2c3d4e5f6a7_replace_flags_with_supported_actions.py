"""replace_flags_with_supported_actions.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-14 12:10:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.add_column(
        "sys_modules",
        sa.Column(
            "supported_actions",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.drop_column("sys_modules", "is_trasheable")
    op.drop_column("sys_modules", "is_exportable")


def downgrade() -> None:
    """Revert the migration."""
    op.add_column(
        "sys_modules",
        sa.Column(
            "is_exportable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "sys_modules",
        sa.Column(
            "is_trasheable",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.drop_column("sys_modules", "supported_actions")
