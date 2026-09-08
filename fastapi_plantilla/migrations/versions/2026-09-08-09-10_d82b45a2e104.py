"""create_sys_trash_bin_table.

Revision ID: d82b45a2e104
Revises: c71a39f1e802
Create Date: 2026-09-08 09:10:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "d82b45a2e104"
down_revision = "c71a39f1e802"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration creating sys_trash_bin."""
    op.create_table(
        "sys_trash_bin",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "data_backup",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["auth_users.id"],
            name=op.f("fk_sys_trash_bin_owner_id_auth_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_trash_bin")),
        sa.UniqueConstraint(
            "entity_type", "entity_id", name="uq_sys_trash_bin_entity"
        ),
    )
    op.create_index(
        op.f("ix_sys_trash_bin_entity_type_entity_id"),
        "sys_trash_bin",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_trash_bin_owner_id"),
        "sys_trash_bin",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_trash_bin_expires_at",
        "sys_trash_bin",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration creating sys_trash_bin."""
    op.drop_index("ix_sys_trash_bin_expires_at", table_name="sys_trash_bin")
    op.drop_index(
        op.f("ix_sys_trash_bin_owner_id"),
        table_name="sys_trash_bin",
    )
    op.drop_index(
        op.f("ix_sys_trash_bin_entity_type_entity_id"),
        table_name="sys_trash_bin",
    )
    op.drop_table("sys_trash_bin")
