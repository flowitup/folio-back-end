"""login_otps.purpose — a code only works for the flow it was issued for

Adds the verified change of the sign-in phone: its codes live in the same table as sign-in /
sign-up codes but carry purpose ``phone_change``, so they can never be exchanged for a session on
/auth/otp/verify, and a sign-in code can never change a number. Every existing row is a sign-in or
sign-up code, hence the ``sign_in`` server default that backfills them.

Downgrade deletes the phone-change codes first (short-lived, meaningless to the older code, and
they would otherwise become usable as sign-in codes), then drops the column.

Revision ID: b4d8e2f6a1c3
Revises: 9a1c7e5f3b2d
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b4d8e2f6a1c3"
down_revision = "9a1c7e5f3b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "login_otps",
        sa.Column("purpose", sa.String(length=20), nullable=False, server_default="sign_in"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM login_otps WHERE purpose <> 'sign_in'")
    op.drop_column("login_otps", "purpose")
