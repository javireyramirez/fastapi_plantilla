"""add_category_details_to_sys_modules.

Revision ID: 0a1b2c3d4e5f
Revises: f6a7b8c9d0e1
Create Date: 2026-09-10 23:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "0a1b2c3d4e5f"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add category_name, category_icon, category_order columns to sys_modules."""
    op.add_column(
        "sys_modules",
        sa.Column("category_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "sys_modules",
        sa.Column("category_icon", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "sys_modules",
        sa.Column(
            "category_order",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop category_name, category_icon, category_order columns from sys_modules."""
    op.drop_column("sys_modules", "category_order")
    op.drop_column("sys_modules", "category_icon")
    op.drop_column("sys_modules", "category_name")
