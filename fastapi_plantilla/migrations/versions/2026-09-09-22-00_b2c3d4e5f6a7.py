"""create_companies_table.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-09 22:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create companies table."""
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("nif", sa.String(length=50), nullable=False),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="ACTIVE", nullable=False
        ),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
        sa.Column("restored_by", sa.String(length=255), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["auth_users.id"],
            name=op.f("fk_companies_owner_id_auth_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
    )
    op.create_index(op.f("ix_companies_name"), "companies", ["name"], unique=False)
    op.create_index(op.f("ix_companies_nif"), "companies", ["nif"], unique=True)
    op.create_index(op.f("ix_companies_sector"), "companies", ["sector"], unique=False)
    op.create_index(
        op.f("ix_companies_owner_id"), "companies", ["owner_id"], unique=False
    )
    op.create_index(op.f("ix_companies_status"), "companies", ["status"], unique=False)


def downgrade() -> None:
    """Drop companies table."""
    op.drop_index(op.f("ix_companies_status"), table_name="companies")
    op.drop_index(op.f("ix_companies_owner_id"), table_name="companies")
    op.drop_index(op.f("ix_companies_sector"), table_name="companies")
    op.drop_index(op.f("ix_companies_nif"), table_name="companies")
    op.drop_index(op.f("ix_companies_name"), table_name="companies")
    op.drop_table("companies")
