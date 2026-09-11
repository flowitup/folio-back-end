"""drop users.password_hash — phone + SMS code is the only way to authenticate

Email/password sign-in is gone and invitation acceptance now proves a phone
number by SMS code instead of collecting a password (see the migrations
before this one in the same removal). Nothing in the application hashes or
checks a password anymore, so the column itself goes too.

downgrade() only restores the column's shape, not its data: the Argon2
hashes are not recoverable once dropped. A rollback gets a nullable
String(128) column back, not working passwords.

Revision ID: 817edd6c5492
Revises: c4d8e2b0f713
Create Date: 2026-09-11
"""

from alembic import op
import sqlalchemy as sa

revision = "817edd6c5492"
down_revision = "c4d8e2b0f713"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "password_hash")


def downgrade() -> None:
    # Schema-only restore: same shape as the original column (Argon2 hashes
    # were ~97 chars, hence String(128)). The hashes themselves are gone for
    # good — this brings back an empty, nullable column, not working logins.
    op.add_column("users", sa.Column("password_hash", sa.String(128), nullable=True))
