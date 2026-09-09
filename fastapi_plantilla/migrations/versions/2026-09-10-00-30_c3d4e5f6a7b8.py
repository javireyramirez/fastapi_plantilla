"""add_module_and_role_metadata_fields.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-10 00:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add category, icon, sort_order to sys_modules and color, icon to rbac_roles."""
    # sys_modules
    op.add_column(
        "sys_modules",
        sa.Column(
            "category", sa.String(length=50), server_default="system", nullable=False
        ),
    )
    op.add_column(
        "sys_modules",
        sa.Column("icon", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "sys_modules",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )

    # rbac_roles
    op.add_column(
        "rbac_roles",
        sa.Column("color", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "rbac_roles",
        sa.Column("icon", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    """Revert addition of metadata fields."""
    op.drop_column("rbac_roles", "icon")
    op.drop_column("rbac_roles", "color")
    op.drop_column("sys_modules", "sort_order")
    op.drop_column("sys_modules", "icon")
    op.drop_column("sys_modules", "category")
