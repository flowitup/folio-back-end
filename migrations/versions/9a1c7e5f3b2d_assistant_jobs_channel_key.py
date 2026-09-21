"""assistant_jobs.channel_key

Phase 03's answer to phase 01/02's open question 2: `fetch_invoice`/`find_product` async
jobs must carry the originating channel so `on_result` (`process_fetched_invoice` /
`process_product_search`) posts its reply back into that same company/project/admin
channel instead of the retired `assistant:<user_id>` fallback. The asker itself already
travels on the row as `assistant_jobs.user_id` — only the channel key was missing.

Nullable: existing (pre-phase-03) rows have no channel to backfill and stay reachable
through their historical NULL value; `on_result` falls back to the messenger's own
`assistant:<user_id>` default for those.

Revision ID: 9a1c7e5f3b2d
Revises: 47c4c56d0cbd
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9a1c7e5f3b2d"
down_revision = "47c4c56d0cbd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_jobs", sa.Column("channel_key", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_jobs", "channel_key")
