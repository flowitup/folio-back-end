"""add_api_keys

Personal automation credentials: a key inherits its owner's permissions in
full (no scope, no company pinning) and never expires — there is
deliberately no expires_at column.

Revision ID: 4175995468e3
Revises: f789318ff9fe
Create Date: 2026-09-12

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4175995468e3"
down_revision = "f789318ff9fe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_created", "api_keys", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_user_created", table_name="api_keys")
    op.drop_table("api_keys")
