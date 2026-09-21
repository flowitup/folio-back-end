"""assistant_jobs

Feature B (invoice fetch via the browser worker): one row per merchant-invoice fetch
job. The ``ai-browser`` container polls this table directly with plain SQL (it never
boots the Flask app — see ``app.infrastructure.browser_worker``), claiming a row with
``SELECT ... FOR UPDATE SKIP LOCKED`` so exactly one worker instance ever processes a
given job even under concurrent pollers.

``status_message_id`` points at the ``job_status`` chat message the assistant posted
when the job was queued; both ``InvoiceFetchFeature`` and the browser worker update that
message's payload in place as the job's state changes (queued -> running ->
not_ready|blocked|done|failed|not_found).

Revision ID: 22f80fb10dad
Revises: c832261b4903
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "22f80fb10dad"
down_revision = "c832261b4903"
branch_labels = None
depends_on = None

# JSONB on Postgres — matches invoice_ai_imports.flags / chat_messages.payload.
ResultJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "assistant_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("merchant", sa.String(length=32), nullable=False),
        sa.Column("amount_ttc", sa.Numeric(12, 2), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("project_hint", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", ResultJSON, nullable=True),
        sa.Column("pdf_storage_key", sa.String(length=512), nullable=True),
        sa.Column("status_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["status_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_jobs_status_run_after", "assistant_jobs", ["status", "run_after"])
    op.create_index("ix_assistant_jobs_user_id", "assistant_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_assistant_jobs_user_id", table_name="assistant_jobs")
    op.drop_index("ix_assistant_jobs_status_run_after", table_name="assistant_jobs")
    op.drop_table("assistant_jobs")
