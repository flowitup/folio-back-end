"""assistant_chat_messages

Adds the Folio Assistant conversation to team chat: a new virtual channel kind
(``assistant:<user_id>``) whose messages can be authored by the assistant itself rather
than by a user.

- ``chat_messages.sender_id`` becomes nullable — NULL means "authored by the assistant".
- New columns: ``sender_type`` (user|assistant|system), ``content_type``
  (text|photo|card|choice|job_status), ``payload`` (JSON, e.g. a card/choice/job_status
  body), ``reply_to_id`` (self-FK, ON DELETE SET NULL — the message a choice answers),
  ``ai_trace_id`` (opaque tracing id for the pipeline that produced the reply).

No data backfill: every existing row already has a non-null sender_id, and the two new
string columns default to the values that describe every row created so far ("user",
"text").

Downgrade restores ``sender_id NOT NULL`` only when no assistant-authored row exists
(sender_id IS NULL); it aborts instead of dropping those rows so a downgrade never
silently deletes assistant chat history. Delete or backfill them by hand first if a
downgrade past this revision is genuinely needed.

Revision ID: b3d7e2f6a1c9
Revises: f2a9c4d17b3e
Create Date: 2026-09-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "b3d7e2f6a1c9"
down_revision = "f2a9c4d17b3e"
branch_labels = None
depends_on = None

# JSONB on Postgres — matches app/infrastructure/database/models/chat_message.py's
# PayloadJSON (JSON generic elsewhere, e.g. SQLite in tests, which never run this
# migration: tests build the schema from the ORM metadata directly).
PayloadJSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.alter_column(
        "chat_messages",
        "sender_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.add_column("chat_messages", sa.Column("sender_type", sa.String(16), nullable=False, server_default="user"))
    op.add_column("chat_messages", sa.Column("content_type", sa.String(16), nullable=False, server_default="text"))
    op.add_column("chat_messages", sa.Column("payload", PayloadJSON, nullable=True))
    op.add_column("chat_messages", sa.Column("reply_to_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("chat_messages", sa.Column("ai_trace_id", sa.String(64), nullable=True))
    op.create_index("ix_chat_messages_reply_to_id", "chat_messages", ["reply_to_id"])
    op.create_foreign_key(
        "fk_chat_messages_reply_to_id",
        "chat_messages",
        "chat_messages",
        ["reply_to_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    orphan_count = bind.execute(sa.text("SELECT COUNT(*) FROM chat_messages WHERE sender_id IS NULL")).scalar()
    if orphan_count:
        raise RuntimeError(
            f"Cannot downgrade: {orphan_count} assistant-authored chat_messages row(s) have "
            "sender_id IS NULL. Delete or backfill them before downgrading past b3d7e2f6a1c9."
        )
    op.drop_constraint("fk_chat_messages_reply_to_id", "chat_messages", type_="foreignkey")
    op.drop_index("ix_chat_messages_reply_to_id", table_name="chat_messages")
    op.drop_column("chat_messages", "ai_trace_id")
    op.drop_column("chat_messages", "reply_to_id")
    op.drop_column("chat_messages", "payload")
    op.drop_column("chat_messages", "content_type")
    op.drop_column("chat_messages", "sender_type")
    op.alter_column(
        "chat_messages",
        "sender_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
