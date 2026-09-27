"""users.tokens_valid_after — sign a user out of every other device at once

A verified change of the sign-in phone ends every other session of the account: tokens issued
before this instant are refused by the per-request token check (``jwt_handlers``), while the
session that made the change receives fresh tokens. Revocation by JTI cannot do this on its own:
refresh tokens held by other devices (mobile ones never expire) are never presented to the server
at change time. NULL — every existing row — means no such cut-off.

Downgrade drops the column: the older code has no notion of it, and sessions it had ended become
valid again until they expire.

Revision ID: d7a3c5e9b214
Revises: b4d8e2f6a1c3
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d7a3c5e9b214"
down_revision = "b4d8e2f6a1c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("tokens_valid_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_after")
