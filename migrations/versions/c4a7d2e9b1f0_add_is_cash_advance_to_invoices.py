"""Add is_cash_advance flag to invoices (company cash handed to a person).

A released_funds invoice flagged is_cash_advance records money the company
handed over (typically cash) to a person who pays site expenses on its
behalf. It is an internal transfer, not a fund release into the project, so
it is excluded from every funds_released total and reported separately as
company_cash_advanced_total (the company purse shows its spend as
"incl. X cash advance"). Only settable when type == 'released_funds'
(enforced at the application layer, not here).

NOT NULL with a server default of false so existing rows and older
application code keep working unchanged.

Revision ID: c4a7d2e9b1f0
Revises: c7d8e9f0a1b2
Create Date: 2026-09-06 01:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4a7d2e9b1f0"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("is_cash_advance", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("invoices", "is_cash_advance")
