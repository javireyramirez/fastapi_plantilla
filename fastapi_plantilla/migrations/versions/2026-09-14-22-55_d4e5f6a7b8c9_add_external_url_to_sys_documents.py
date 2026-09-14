"""add_external_url_to_sys_documents.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-14 22:55:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.add_column(
        "sys_documents",
        sa.Column("external_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("sys_documents", "external_url")
