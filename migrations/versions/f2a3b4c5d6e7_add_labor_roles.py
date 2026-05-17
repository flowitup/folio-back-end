"""Add labor_roles table, seed default roles, add role_id to workers, drop avatar_url.

Revision ID: f2a3b4c5d6e7
Revises: e8a2c4d6f1b3
Create Date: 2026-05-18 00:00:00.000000

"""

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = ("5a85f7474a41", "e8a2c4d6f1b3")
branch_labels = None
depends_on = None

# Stable seed UUIDs for the two default labor roles.
_SEED_THO_CHINH = "b08f0bdb-9e78-40ca-aca9-96016de45c7c"
_SEED_THO_PHU = "de417d58-3d38-4658-a5f7-02b51fb749fc"


def upgrade() -> None:
    # 1. Create labor_roles table
    op.create_table(
        "labor_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("color", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # 2. Seed default roles
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    op.execute(
        f"INSERT INTO labor_roles (id, name, color, created_at) VALUES "
        f"('{_SEED_THO_CHINH}', 'Thợ chính', '#3B82F6', '{now}'), "
        f"('{_SEED_THO_PHU}', 'Thợ phụ', '#10B981', '{now}')"
    )

    # 3. Add role_id FK column to workers (nullable, indexed, SET NULL on delete)
    op.add_column(
        "workers",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("labor_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_workers_role_id", "workers", ["role_id"])

    # 4. Drop avatar_url column from workers
    op.drop_column("workers", "avatar_url")


def downgrade() -> None:
    # 1. Restore avatar_url column
    op.add_column(
        "workers",
        sa.Column("avatar_url", sa.String(500), nullable=True),
    )

    # 2. Remove role_id index and column
    op.drop_index("ix_workers_role_id", table_name="workers")
    op.drop_column("workers", "role_id")

    # 3. Drop labor_roles table (seed rows removed with it)
    op.drop_table("labor_roles")
