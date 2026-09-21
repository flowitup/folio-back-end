"""ORM model for ``assistant_audit_log`` — one row per assistant mention the pipeline
handled (D17 layer 4: "audit row per handled mention", readable from the admin channel
and the web supervision page in a later phase).

Kept append-only and cheap to write: no foreign key on ``channel_key`` (channels are
virtual, see ``app.domain.entities.chat_message``) and ``company_id``/``message_id`` are
nullable so a row can still be written for a channel kind or a pipeline branch that has
no company/message to point at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base

# JSONB on Postgres, generic JSON elsewhere (SQLite in tests) — same pattern as
# chat_messages.payload / assistant_jobs.result.
ToolsJSON = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssistantAuditLogModel(Base):
    __tablename__ = "assistant_audit_log"
    __table_args__ = (Index("ix_assistant_audit_log_company_created", "company_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    channel_key: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    feature: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    tools: Mapped[Optional[Any]] = mapped_column(ToolsJSON, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    refused_reason: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 5), nullable=False, default=Decimal("0"))
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
