"""add_sys_settings.

Revision ID: a1b2c3d4e5f6
Revises: ffab4e4ee76c
Create Date: 2026-09-13 23:35:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "ffab4e4ee76c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "sys_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "category",
            sa.String(length=50),
            server_default=sa.text("'general'"),
            nullable=False,
        ),
        sa.Column(
            "is_public",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_settings")),
    )
    op.create_index(
        op.f("ix_sys_settings_key"),
        "sys_settings",
        ["key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_sys_settings_category"),
        "sys_settings",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_settings_is_public"),
        "sys_settings",
        ["is_public"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_sys_settings_is_public"), table_name="sys_settings")
    op.drop_index(op.f("ix_sys_settings_category"), table_name="sys_settings")
    op.drop_index(op.f("ix_sys_settings_key"), table_name="sys_settings")
    op.drop_table("sys_settings")
