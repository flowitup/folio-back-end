"""drop company_invite_tokens — join code is now the only way to attach

The single-use invite-token flow (generate/revoke/redeem use-cases, POST
/companies/<id>/invite-tokens, DELETE .../invite-tokens/active, POST
/companies/attach-by-token) is removed from the API and application layers.
The reusable join code (companies.join_code + POST /companies/join) is now
the sole mechanism to bring a user into a company. Safe to drop: zero
unredeemed tokens existed in production at removal time (2 ever created,
both redeemed, last on 2026-06-01), and nothing holds a foreign key into
this table.

downgrade() only restores the table's shape — id, company_id, token_hash,
created_by, created_at, expires_at, redeemed_at, redeemed_by, role — plus
its two indexes, the role CHECK constraint, and the three foreign keys.
No data is recoverable: a rollback gets an empty table back, not the
historical tokens.

Revision ID: f789318ff9fe
Revises: 817edd6c5492
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa

revision = "f789318ff9fe"
down_revision = "817edd6c5492"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "uix_company_invite_tokens_active_per_company",
        table_name="company_invite_tokens",
    )
    op.drop_index("ix_company_invite_tokens_company_id", table_name="company_invite_tokens")
    op.drop_table("company_invite_tokens")


def downgrade() -> None:
    op.create_table(
        "company_invite_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by", sa.UUID(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["redeemed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('admin','member')",
            name="ck_company_invite_tokens_role",
        ),
    )
    op.create_index(
        "ix_company_invite_tokens_company_id",
        "company_invite_tokens",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "uix_company_invite_tokens_active_per_company",
        "company_invite_tokens",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("redeemed_at IS NULL"),
    )
