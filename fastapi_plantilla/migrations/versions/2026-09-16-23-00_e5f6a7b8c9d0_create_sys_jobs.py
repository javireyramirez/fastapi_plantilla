"""create_sys_jobs.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-16 23:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run the migration."""
    op.create_table(
        "sys_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="jobstatus",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_message", sa.String(length=255), nullable=True),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "lease_duration_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
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
            ["created_by_id"], ["auth_users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_sys_jobs_name",
        "sys_jobs",
        ["name"],
        unique=False,
    )
    op.create_index(
        "ix_sys_jobs_status",
        "sys_jobs",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_sys_jobs_scheduled_at",
        "sys_jobs",
        ["scheduled_at"],
        unique=False,
    )
    op.create_index(
        "ix_sys_jobs_created_by_id",
        "sys_jobs",
        ["created_by_id"],
        unique=False,
    )
    op.create_index(
        "idx_sys_jobs_entity",
        "sys_jobs",
        ["entity_type", "entity_id"],
        unique=False,
    )
    op.create_index(
        "idx_sys_jobs_claim_pending",
        "sys_jobs",
        ["scheduled_at", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "idx_sys_jobs_claim_expired_leases",
        "sys_jobs",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "uq_sys_jobs_idempotency",
        "sys_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("uq_sys_jobs_idempotency", table_name="sys_jobs")
    op.drop_index("idx_sys_jobs_claim_expired_leases", table_name="sys_jobs")
    op.drop_index("idx_sys_jobs_claim_pending", table_name="sys_jobs")
    op.drop_index("idx_sys_jobs_entity", table_name="sys_jobs")
    op.drop_index("ix_sys_jobs_created_by_id", table_name="sys_jobs")
    op.drop_index("ix_sys_jobs_scheduled_at", table_name="sys_jobs")
    op.drop_index("ix_sys_jobs_status", table_name="sys_jobs")
    op.drop_index("ix_sys_jobs_name", table_name="sys_jobs")
    op.drop_table("sys_jobs")