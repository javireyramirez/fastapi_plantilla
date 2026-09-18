"""partial_unique_index_auth_users_email.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-09-19 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.drop_index("ix_auth_users_email", table_name="auth_users")
    op.create_index(
        "ix_auth_users_email_active",
        "auth_users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("status != 'TRASHED'"),
        sqlite_where=sa.text("status != 'TRASHED'"),
    )
    op.create_index(
        op.f("ix_auth_users_email"), "auth_users", ["email"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_auth_users_email", table_name="auth_users")
    op.drop_index("ix_auth_users_email_active", table_name="auth_users")
    op.create_index(
        op.f("ix_auth_users_email"), "auth_users", ["email"], unique=True
    )
