"""Worker self-logged attendance → manager validation.

- labor_entries.status ('pending' | 'validated', default 'validated' so every
  existing row stays priced), submitted_by / validated_by / validated_at audit.
- workers.user_id: the signed-in account that may log its own days for this
  worker; one account is at most one worker per project.
- permission project:log_own_attendance granted to the member and user roles.

Revision ID: b633c91f9fbe
Revises: f7a8b9c0d1e2
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b633c91f9fbe"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "labor_entries",
        sa.Column("status", sa.String(20), nullable=False, server_default="validated"),
    )
    op.add_column(
        "labor_entries",
        sa.Column(
            "submitted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_submitted_by"),
            nullable=True,
        ),
    )
    op.add_column(
        "labor_entries",
        sa.Column(
            "validated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_labor_entries_validated_by"),
            nullable=True,
        ),
    )
    op.add_column("labor_entries", sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_labor_entries_status",
        "labor_entries",
        "status IN ('pending', 'validated')",
    )
    # Pending rows are few and read on every bell poll — keep them cheap to find.
    op.create_index(
        "ix_labor_entries_pending",
        "labor_entries",
        ["worker_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.add_column(
        "workers",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_workers_user_id"),
            nullable=True,
        ),
    )
    # Postgres unique constraints ignore NULLs, so unlinked workers stay unconstrained.
    op.create_unique_constraint("uq_workers_project_user", "workers", ["project_id", "user_id"])

    op.execute(
        """
        INSERT INTO permissions (id, name, resource, action)
        VALUES (gen_random_uuid(), 'project:log_own_attendance', 'project', 'log_own_attendance')
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.name IN ('member', 'user') AND p.name = 'project:log_own_attendance'
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE name = 'project:log_own_attendance';")
    op.drop_constraint("uq_workers_project_user", "workers", type_="unique")
    op.drop_constraint("fk_workers_user_id", "workers", type_="foreignkey")
    op.drop_column("workers", "user_id")
    op.drop_index("ix_labor_entries_pending", table_name="labor_entries")
    op.drop_constraint("ck_labor_entries_status", "labor_entries", type_="check")
    op.drop_column("labor_entries", "validated_at")
    op.drop_constraint("fk_labor_entries_validated_by", "labor_entries", type_="foreignkey")
    op.drop_column("labor_entries", "validated_by_user_id")
    op.drop_constraint("fk_labor_entries_submitted_by", "labor_entries", type_="foreignkey")
    op.drop_column("labor_entries", "submitted_by_user_id")
    op.drop_column("labor_entries", "status")
