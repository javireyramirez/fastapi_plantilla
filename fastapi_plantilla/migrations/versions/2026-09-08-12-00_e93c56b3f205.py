"""create_rbac_teams_and_impersonation_tables.

Revision ID: e93c56b3f205
Revises: d82b45a2e104
Create Date: 2026-09-08 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e93c56b3f205"
down_revision = "d82b45a2e104"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Run migration creating RBAC, Teams, and Impersonation schema."""
    # 1. sys_modules
    op.create_table(
        "sys_modules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_modules")),
    )
    op.create_index(op.f("ix_sys_modules_code"), "sys_modules", ["code"], unique=True)
    op.create_index(
        op.f("ix_sys_modules_is_active"), "sys_modules", ["is_active"], unique=False
    )

    # 2. rbac_roles
    op.create_table(
        "rbac_roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "status", sa.String(length=20), server_default="ACTIVE", nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
        sa.Column("restored_by", sa.String(length=255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rbac_roles")),
    )
    op.create_index(op.f("ix_rbac_roles_slug"), "rbac_roles", ["slug"], unique=True)
    op.create_index(
        op.f("ix_rbac_roles_status"), "rbac_roles", ["status"], unique=False
    )

    # 3. rbac_role_permissions
    op.create_table(
        "rbac_role_permissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("module_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default="OWN"),
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
            ["module_id"],
            ["sys_modules.id"],
            name=op.f("fk_rbac_role_permissions_module_id_sys_modules"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["rbac_roles.id"],
            name=op.f("fk_rbac_role_permissions_role_id_rbac_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rbac_role_permissions")),
        sa.UniqueConstraint(
            "role_id", "module_id", "action", name="uq_role_permission_action"
        ),
    )
    op.create_index(
        op.f("ix_rbac_role_permissions_role_id"),
        "rbac_role_permissions",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_role_permissions_module_id"),
        "rbac_role_permissions",
        ["module_id"],
        unique=False,
    )

    # 4. rbac_role_assignments
    op.create_table(
        "rbac_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
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
            ["role_id"],
            ["rbac_roles.id"],
            name=op.f("fk_rbac_role_assignments_role_id_rbac_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rbac_role_assignments")),
        sa.UniqueConstraint(
            "role_id", "entity_type", "entity_id", name="uq_role_assignment"
        ),
    )
    op.create_index(
        op.f("ix_rbac_role_assignments_role_id"),
        "rbac_role_assignments",
        ["role_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_role_assignments_entity_type"),
        "rbac_role_assignments",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_rbac_role_assignments_entity_id"),
        "rbac_role_assignments",
        ["entity_id"],
        unique=False,
    )

    # 5. sys_teams
    op.create_table(
        "sys_teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default="ACTIVE", nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("deleted_by", sa.String(length=255), nullable=True),
        sa.Column("restored_by", sa.String(length=255), nullable=True),
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
            name=op.f("fk_sys_teams_owner_id_auth_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_teams")),
    )
    op.create_index(op.f("ix_sys_teams_slug"), "sys_teams", ["slug"], unique=True)
    op.create_index(op.f("ix_sys_teams_status"), "sys_teams", ["status"], unique=False)
    op.create_index(
        op.f("ix_sys_teams_owner_id"), "sys_teams", ["owner_id"], unique=False
    )

    # 6. sys_team_users
    op.create_table(
        "sys_team_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=True),
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
            ["role_id"],
            ["rbac_roles.id"],
            name=op.f("fk_sys_team_users_role_id_rbac_roles"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["sys_teams.id"],
            name=op.f("fk_sys_team_users_team_id_sys_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["auth_users.id"],
            name=op.f("fk_sys_team_users_user_id_auth_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_team_users")),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_user"),
    )
    op.create_index(
        op.f("ix_sys_team_users_team_id"), "sys_team_users", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_sys_team_users_user_id"), "sys_team_users", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_sys_team_users_role_id"), "sys_team_users", ["role_id"], unique=False
    )

    # 7. Alter auth_sessions: add impersonated_by
    op.add_column(
        "auth_sessions",
        sa.Column("impersonated_by", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_auth_sessions_impersonated_by_auth_users"),
        "auth_sessions",
        "auth_users",
        ["impersonated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_auth_sessions_impersonated_by"),
        "auth_sessions",
        ["impersonated_by"],
        unique=False,
    )

    # 8. Alter auth_users: add audit and optimistic lock columns
    op.add_column(
        "auth_users",
        sa.Column(
            "status", sa.String(length=20), server_default="ACTIVE", nullable=False
        ),
    )
    op.add_column(
        "auth_users",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "auth_users",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "auth_users",
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "auth_users", sa.Column("created_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "auth_users", sa.Column("updated_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "auth_users", sa.Column("deleted_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "auth_users", sa.Column("restored_by", sa.String(length=255), nullable=True)
    )
    op.create_index(
        op.f("ix_auth_users_status"), "auth_users", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema removing RBAC, Teams, and Impersonation tables."""
    op.drop_index(op.f("ix_auth_users_status"), table_name="auth_users")
    op.drop_column("auth_users", "restored_by")
    op.drop_column("auth_users", "deleted_by")
    op.drop_column("auth_users", "updated_by")
    op.drop_column("auth_users", "created_by")
    op.drop_column("auth_users", "restored_at")
    op.drop_column("auth_users", "deleted_at")
    op.drop_column("auth_users", "version")
    op.drop_column("auth_users", "status")

    op.drop_constraint(
        op.f("fk_auth_sessions_impersonated_by_auth_users"),
        "auth_sessions",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_auth_sessions_impersonated_by"), table_name="auth_sessions")
    op.drop_column("auth_sessions", "impersonated_by")

    op.drop_table("sys_team_users")
    op.drop_table("sys_teams")
    op.drop_table("rbac_role_assignments")
    op.drop_table("rbac_role_permissions")
    op.drop_table("rbac_roles")
    op.drop_table("sys_modules")
