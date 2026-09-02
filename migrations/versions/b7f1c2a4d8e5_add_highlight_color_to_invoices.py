"""Add highlight_color to invoices for the row-highlight feature.

An invoice may carry an optional highlight color (one of a fixed palette) so the
UI can tint its list row. NULL means no highlight. Applies to invoices of every
type; a purely visual annotation with no financial meaning.

highlight_color is a plain VARCHAR with a CHECK constraint rather than a Postgres
enum type — adding new palette values later needs no ALTER TYPE migration
(mirrors settled_via).

Revision ID: b7f1c2a4d8e5
Revises: e4a7c26d91f8
"""

import sqlalchemy as sa
from alembic import op

revision = "b7f1c2a4d8e5"
down_revision = "e4a7c26d91f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("highlight_color", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_invoices_highlight_color",
        "invoices",
        "highlight_color IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoices_highlight_color", "invoices", type_="check")
    op.drop_column("invoices", "highlight_color")
