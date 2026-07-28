"""Add is_personal_payment flag to payment_methods.

Marks payment methods that represent direct personal (out-of-pocket, non-company)
expenditure. Mutually exclusive with is_company_payment — a method cannot be
flagged as both company-funded and personal-funded — enforced by a CHECK
constraint so the invariant holds regardless of write path.

Revision ID: 121ab5361948
Revises: 7e20d88b8197
Create Date: 2026-07-28 09:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "121ab5361948"
down_revision = "7e20d88b8197"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_methods",
        sa.Column(
            "is_personal_payment",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_check_constraint(
        "ck_payment_methods_company_personal_exclusive",
        "payment_methods",
        "NOT (is_company_payment AND is_personal_payment)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payment_methods_company_personal_exclusive", "payment_methods", type_="check")
    op.drop_column("payment_methods", "is_personal_payment")
