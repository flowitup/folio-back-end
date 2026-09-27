"""company_persons.pending_company_role

The company role an admin picks when adding by phone someone who has no
account yet ("manager" rather than the default "member") had nowhere to live,
so the person always joined as a member at signup. The pending profile now
keeps it until the signup links the account, then it is cleared. Nullable:
NULL means the default role, member.

Revision ID: b6d2f4a8c1e3
Revises: 55e0723ce41c
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b6d2f4a8c1e3"
down_revision = "55e0723ce41c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_persons", sa.Column("pending_company_role", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("company_persons", "pending_company_role")
