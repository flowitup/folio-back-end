"""assistant_jobs: nullable merchant/amount_ttc/date + params (find_product jobs)

Owner decision D16: feature A (material photo -> product fiche) now finds the product
with the same browser agent feature B uses, via a new ``assistant_jobs.type ==
"find_product"`` job — dropping Tavily/SerpApi entirely. That job has no fixed
merchant/amount/date to key on (those three columns become nullable, ``fetch_invoice``
still always sets them), and instead carries its own state in a new ``params`` JSON
column: the ``MaterialIdent`` dump, ``search_queries``, ``company_id``,
``photo_sha256`` and ``message_id`` (see ``app.application.assistant.features.material``).

Revision ID: 543e51dec1fe
Revises: 36d71c5b9a08
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "543e51dec1fe"
down_revision = "36d71c5b9a08"
branch_labels = None
depends_on = None

# JSONB on Postgres, generic JSON elsewhere — same pattern as assistant_jobs.result.
_ParamsJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.alter_column("assistant_jobs", "merchant", existing_type=sa.String(length=32), nullable=True)
    op.alter_column("assistant_jobs", "amount_ttc", existing_type=sa.Numeric(12, 2), nullable=True)
    op.alter_column("assistant_jobs", "date", existing_type=sa.Date(), nullable=True)
    op.add_column("assistant_jobs", sa.Column("params", _ParamsJSON, nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_jobs", "params")
    op.alter_column("assistant_jobs", "date", existing_type=sa.Date(), nullable=False)
    op.alter_column("assistant_jobs", "amount_ttc", existing_type=sa.Numeric(12, 2), nullable=False)
    op.alter_column("assistant_jobs", "merchant", existing_type=sa.String(length=32), nullable=False)
