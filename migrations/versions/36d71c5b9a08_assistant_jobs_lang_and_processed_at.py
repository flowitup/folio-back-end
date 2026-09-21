"""assistant_jobs.lang and assistant_jobs.processed_at

Two independent, nullable additions to ``assistant_jobs`` (feature B):

- ``lang``: the requester's language (vi|fr|en), so the ``ai-browser`` container's poll
  loop — which has no chat-repository/messenger wiring to look up the original
  message's ``lang`` payload — can render the transient "running" job_status text
  correctly instead of always French (phase 04 unresolved question 3).
- ``processed_at``: set exactly once, atomically, right before ``on_result``'s "done"
  branch runs the create-invoice pipeline, guarding against a duplicate invoice if
  ``process_fetched_invoice`` is ever invoked twice for the same job (review finding
  H3: an RQ retry policy, a manual requeue, or a double enqueue after a worker crash
  between ``update_result`` and ``enqueue``).

Revision ID: 36d71c5b9a08
Revises: 22f80fb10dad
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "36d71c5b9a08"
down_revision = "22f80fb10dad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_jobs", sa.Column("lang", sa.String(length=5), nullable=True))
    op.add_column("assistant_jobs", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_jobs", "processed_at")
    op.drop_column("assistant_jobs", "lang")
