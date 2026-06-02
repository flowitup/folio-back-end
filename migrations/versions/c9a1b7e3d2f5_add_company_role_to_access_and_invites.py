"""add per-company role to user_company_access and company_invite_tokens

Adds a "role" column (admin|member) to both tables so company billing/member
management can be gated per-company. Existing memberships are backfilled to
'admin' to preserve current access; new memberships default to 'member'.

Revision ID: c9a1b7e3d2f5
Revises: 383b1db5c576
Create Date: 2026-06-02 00:50:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = "c9a1b7e3d2f5"
down_revision = "383b1db5c576"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. user_company_access.role — default 'member', NOT NULL, CHECK admin|member
    op.add_column(
        "user_company_access",
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
    )
    op.create_check_constraint(
        "ck_user_company_access_role",
        "user_company_access",
        "role IN ('admin','member')",
    )
    # Backfill: every existing membership becomes 'admin' so no current member
    # loses billing access when the gate is introduced.
    conn.execute(text("UPDATE user_company_access SET role = 'admin'"))

    # 2. company_invite_tokens.role — default 'member', NOT NULL, CHECK admin|member.
    #    Existing unredeemed tokens keep 'member' (least-privilege default).
    op.add_column(
        "company_invite_tokens",
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
    )
    op.create_check_constraint(
        "ck_company_invite_tokens_role",
        "company_invite_tokens",
        "role IN ('admin','member')",
    )


def downgrade():
    op.drop_constraint("ck_company_invite_tokens_role", "company_invite_tokens", type_="check")
    op.drop_column("company_invite_tokens", "role")
    op.drop_constraint("ck_user_company_access_role", "user_company_access", type_="check")
    op.drop_column("user_company_access", "role")
