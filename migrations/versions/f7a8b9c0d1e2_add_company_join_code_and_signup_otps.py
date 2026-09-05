"""companies.join_code (shared short code to join as member) + sign-up OTPs without a user

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("join_code", sa.String(16), nullable=True))
    op.create_unique_constraint("uq_companies_join_code", "companies", ["join_code"])
    op.alter_column("login_otps", "user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM login_otps WHERE user_id IS NULL")
    op.alter_column("login_otps", "user_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.drop_constraint("uq_companies_join_code", "companies", type_="unique")
    op.drop_column("companies", "join_code")
