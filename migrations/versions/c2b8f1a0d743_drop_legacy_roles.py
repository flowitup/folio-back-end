"""drop the legacy role tables and make projects.company_id mandatory

Destructive and NOT reversible by data: permissions now come from the company
role (`admin` | `manager` | `member`) plus the per-member grant/deny rows, so
`roles`, `permissions`, `user_roles` and `role_permissions` carry nothing the
application still reads. Rolling this release back means restoring a dump.

Drop order matters — the referencing columns go first, then the association
tables, then the two parent tables:

1. ``invitations.role_id``  (pending invitations keep working: the acceptor
   becomes a company member, a role id is no longer part of the invite)
2. ``user_projects.role_id`` (a project assignment carries no role)
3. ``user_roles`` / ``role_permissions``
4. ``roles`` / ``permissions``

``projects.company_id`` becomes NOT NULL. A project without a company cannot
resolve any permission, so it would be invisible to everyone; the upgrade
aborts with the offending ids rather than leaving that state behind.

Postgres only: the ``SET NOT NULL`` below is a bare ``ALTER COLUMN``, which
SQLite cannot run. Every environment this revision targets is Postgres; the
test suite builds its schema from the models instead of replaying migrations.

Revision ID: c2b8f1a0d743
Revises: 9a4c1e7b2d05
Create Date: 2026-09-08 15:40:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c2b8f1a0d743"
down_revision = "9a4c1e7b2d05"
branch_labels = None
depends_on = None


class OrphanProjectsError(RuntimeError):
    """Raised when a project would violate the new NOT NULL on company_id."""


def _abort_on_projects_without_a_company(bind: sa.engine.Connection) -> None:
    """Fail the upgrade before altering anything if any project has no company.

    Unlike the additive backfills of the previous release, this one cannot warn
    and continue: ``ALTER COLUMN SET NOT NULL`` would fail anyway, with an error
    that names neither the rows nor the fix. Repair is manual and deliberate —
    attach the project to its company (or delete it) and redeploy.
    """
    rows = bind.execute(
        sa.text("SELECT id, name FROM projects WHERE company_id IS NULL ORDER BY created_at NULLS LAST LIMIT 50")
    ).fetchall()
    if not rows:
        return
    total = bind.execute(sa.text("SELECT count(*) FROM projects WHERE company_id IS NULL")).scalar_one()
    listed = ", ".join(f"{row[0]} ({row[1]!r})" for row in rows)
    raise OrphanProjectsError(
        f"{total} project(s) still have no company_id and would be unreachable once "
        "permissions resolve through the company. Attach or delete them, then redeploy. "
        f"First {len(rows)}: {listed}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    _abort_on_projects_without_a_company(bind)

    # 1. Referencing columns.
    with op.batch_alter_table("invitations", schema=None) as batch_op:
        batch_op.drop_column("role_id")

    with op.batch_alter_table("user_projects", schema=None) as batch_op:
        batch_op.drop_column("role_id")

    # 2. Association tables (children of both roles and permissions).
    op.drop_table("user_roles")
    op.drop_table("role_permissions")

    # 3. Parent tables.
    op.drop_table("roles")
    op.drop_table("permissions")

    # 4. Every project belongs to a company.
    op.alter_column("projects", "company_id", existing_type=postgresql.UUID(), nullable=False)


def downgrade() -> None:
    """Recreate the schema, empty.

    The rows are gone. Every user would come back with no global role and every
    invitation/assignment with a NULL role id, which the previous release reads
    as "no permission at all" — a downgrade is only safe as part of restoring a
    dump taken before the upgrade.
    """
    print(
        "WARNING: downgrade recreates roles/permissions/user_roles/role_permissions "
        "EMPTY and leaves invitations.role_id and user_projects.role_id NULL. "
        "The previous release needs those rows: restore the pre-upgrade dump."
    )

    op.alter_column("projects", "company_id", existing_type=postgresql.UUID(), nullable=True)

    op.create_table(
        "permissions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_permissions_resource_action", "permissions", ["resource", "action"], unique=False)

    op.create_table(
        "roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("permission_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"], unique=False)
    op.create_index("ix_role_permissions_permission_id", "role_permissions", ["permission_id"], unique=False)

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )

    # Nullable on the way back: there is no role to point at.
    op.add_column("user_projects", sa.Column("role_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_user_projects_role_id",
        "user_projects",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("invitations", sa.Column("role_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_invitations_role_id",
        "invitations",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="RESTRICT",
    )
