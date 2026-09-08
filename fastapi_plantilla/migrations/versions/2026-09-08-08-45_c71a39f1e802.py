"""create_sys_documents_table.

Revision ID: c71a39f1e802
Revises: b4866d68cfb3
Create Date: 2026-09-08 08:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

from fastapi_plantilla.core.mixins import RecordStatus

# revision identifiers, used by Alembic.
revision = "c71a39f1e802"
down_revision = "b4866d68cfb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration creating sys_documents."""
    op.create_table(
        "sys_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_key", sa.String(length=500), nullable=False),
        sa.Column(
            "content_type",
            sa.String(length=100),
            server_default="application/octet-stream",
            nullable=False,
        ),
        sa.Column(
            "size_bytes", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("extension", sa.String(length=20), nullable=True),
        sa.Column(
            "is_uploaded",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(RecordStatus, native_enum=False, length=20),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
        sa.Column("restored_by", sa.String(length=255), nullable=True),
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["auth_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sys_documents_entity_type"),
        "sys_documents",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_documents_entity_id"),
        "sys_documents",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        "ix_sys_documents_entity",
        "sys_documents",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_documents_file_key"),
        "sys_documents",
        ["file_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_sys_documents_is_uploaded"),
        "sys_documents",
        ["is_uploaded"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_documents_owner_id"),
        "sys_documents",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sys_documents_status"),
        "sys_documents",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    """Revert sys_documents table creation."""
    op.drop_index(op.f("ix_sys_documents_status"), table_name="sys_documents")
    op.drop_index(op.f("ix_sys_documents_owner_id"), table_name="sys_documents")
    op.drop_index(op.f("ix_sys_documents_is_uploaded"), table_name="sys_documents")
    op.drop_index(op.f("ix_sys_documents_file_key"), table_name="sys_documents")
    op.drop_index("ix_sys_documents_entity", table_name="sys_documents")
    op.drop_index(op.f("ix_sys_documents_entity_id"), table_name="sys_documents")
    op.drop_index(op.f("ix_sys_documents_entity_type"), table_name="sys_documents")
    op.drop_table("sys_documents")
