"""add_two_factor_to_auth_users.

Revision ID: a8b9c0d1e2f3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-18 20:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8b9c0d1e2f3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.add_column(
        "auth_users",
        sa.Column(
            "two_factor_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "auth_users",
        sa.Column(
            "two_factor_secret",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.add_column(
        "auth_users",
        sa.Column(
            "two_factor_backup_codes",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("auth_users", "two_factor_backup_codes")
    op.drop_column("auth_users", "two_factor_secret")
    op.drop_column("auth_users", "two_factor_enabled")
