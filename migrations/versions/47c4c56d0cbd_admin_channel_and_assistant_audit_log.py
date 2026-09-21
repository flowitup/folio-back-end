"""chat_messages.mentions_assistant + assistant_audit_log

Owner decisions D17-D19 (assistant-channels plan, phase 01): the assistant no longer
lives in a private per-user ``assistant:<user_id>`` channel — it answers inside the
existing company/project channels (and a new admin-only channel per company) when
addressed with ``@folio`` or a reply to one of its own messages. This migration adds the
two pieces of storage that decision needs:

* ``chat_messages.mentions_assistant`` — set at send time, read back by
  ``list_recent_addressed`` to build the assistant's context window.
* ``assistant_audit_log`` — one row per handled mention (D17 layer 4), later readable
  from the admin channel and a web supervision page.

The retired ``"assistant"`` channel kind is an application-layer concept only (see
``app.domain.entities.chat_message.CHANNEL_KINDS``): existing ``chat_messages`` rows with
``channel_kind = 'assistant'`` are deliberately left untouched here — they simply become
unreachable (every route 404s on that key) rather than being deleted or migrated.

Revision ID: 47c4c56d0cbd
Revises: 543e51dec1fe
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "47c4c56d0cbd"
down_revision = "543e51dec1fe"
branch_labels = None
depends_on = None

# JSONB on Postgres, generic JSON elsewhere — same pattern as chat_messages.payload.
_ToolsJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("mentions_assistant", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "assistant_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_key", sa.String(length=80), nullable=False),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intent", sa.String(length=40), nullable=True),
        sa.Column("feature", sa.String(length=24), nullable=True),
        sa.Column("tools", _ToolsJSON, nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("refused_reason", sa.String(length=40), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 5), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_audit_log_company_created", "assistant_audit_log", ["company_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_audit_log_company_created", table_name="assistant_audit_log")
    op.drop_table("assistant_audit_log")
    op.drop_column("chat_messages", "mentions_assistant")
