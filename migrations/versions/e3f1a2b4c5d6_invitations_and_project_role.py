"""invitations_and_project_role

Revision ID: e3f1a2b4c5d6
Revises: 0d18fab50ecb
Create Date: 2026-04-27 02:00:00.000000

"""

import logging

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e3f1a2b4c5d6"
down_revision = "0d18fab50ecb"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade():
    conn = op.get_bind()

    # 1. Add users.display_name (nullable)
    op.add_column("users", sa.Column("display_name", sa.Text(), nullable=True))

    # 2. Create invitations table
    op.create_table(
        "invitations",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','accepted','revoked','expired')",
            name="ck_invitations_status",
        ),
        sa.CheckConstraint(
            "lower(email) = email",
            name="ck_invitations_email_lowercase",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )
    op.create_index("ix_invitations_email", "invitations", ["email"], unique=False)
    op.create_index(
        "ix_invitations_project_id_status",
        "invitations",
        ["project_id", "status"],
        unique=False,
    )
    # Partial unique index: only one pending invitation per (email, project_id)
    # Postgres-only syntax; use op.execute for WHERE clause support
    op.execute(
        "CREATE UNIQUE INDEX uq_invitations_pending_email_project "
        "ON invitations (email, project_id) WHERE status = 'pending'"
    )

    # 3. Seed 'member' role (ON CONFLICT DO NOTHING — idempotent)
    conn.execute(
        sa.text(
            """
            INSERT INTO roles (id, name, description, created_at)
            VALUES (gen_random_uuid(), 'member', 'Default project member', NOW())
            ON CONFLICT (name) DO NOTHING
            """
        )
    )

    # Fetch member role id — must exist after seed; raise if missing (guards backfill)
    result = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'member'"))
    member_role_row = result.fetchone()
    if member_role_row is None:
        raise RuntimeError("Failed to seed 'member' role — cannot proceed with backfill.")
    member_role_id = member_role_row[0]

    # 4. Seed 'project:invite' permission + attach to admin role if it exists
    conn.execute(
        sa.text(
            """
            INSERT INTO permissions (id, name, resource, action, created_at)
            VALUES (gen_random_uuid(), 'project:invite', 'project', 'invite', NOW())
            ON CONFLICT (name) DO NOTHING
            """
        )
    )

    result = conn.execute(sa.text("SELECT id FROM permissions WHERE name = 'project:invite'"))
    perm_row = result.fetchone()
    if perm_row is None:
        raise RuntimeError("Failed to seed 'project:invite' permission — unexpected conflict state.")
    perm_id = perm_row[0]

    result = conn.execute(sa.text("SELECT id FROM roles WHERE name = 'admin'"))
    admin_row = result.fetchone()
    if admin_row is None:
        logger.warning(
            "Role 'admin' not found — skipping grant of 'project:invite' permission. "
            "Grant manually or via phase-09 seed fixtures."
        )
    else:
        admin_role_id = admin_row[0]
        conn.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES (:role_id, :perm_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"role_id": str(admin_role_id), "perm_id": str(perm_id)},
        )

    # 5. Add nullable columns to user_projects first
    op.add_column(
        "user_projects",
        sa.Column("role_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "user_projects",
        sa.Column("invited_by_user_id", sa.UUID(), nullable=True),
    )

    # 6. Backfill role_id to member role for all existing rows
    conn.execute(
        sa.text("UPDATE user_projects SET role_id = :member_id WHERE role_id IS NULL"),
        {"member_id": str(member_role_id)},
    )

    # 7. Alter role_id to NOT NULL
    op.alter_column("user_projects", "role_id", nullable=False)

    # 8. Add FK constraints for the new user_projects columns
    op.create_foreign_key(
        "fk_user_projects_role_id",
        "user_projects",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_user_projects_invited_by_user_id",
        "user_projects",
        "users",
        ["invited_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    # Drop FKs on user_projects
    op.drop_constraint("fk_user_projects_invited_by_user_id", "user_projects", type_="foreignkey")
    op.drop_constraint("fk_user_projects_role_id", "user_projects", type_="foreignkey")

    # Drop new columns from user_projects
    op.drop_column("user_projects", "invited_by_user_id")
    op.drop_column("user_projects", "role_id")

    # Drop invitations indexes and table
    op.execute("DROP INDEX IF EXISTS uq_invitations_pending_email_project")
    op.drop_index("ix_invitations_project_id_status", table_name="invitations")
    op.drop_index("ix_invitations_email", table_name="invitations")
    op.drop_table("invitations")

    # Drop display_name from users
    op.drop_column("users", "display_name")

    # NOTE: seeded 'member' role, 'project:invite' permission, and admin grant are
    # intentionally NOT removed — they are data seeds, not schema objects.
