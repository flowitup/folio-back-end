"""ORM model for ``assistant_audit_log``.

LEGACY / INERT: the feature that used this table has been removed; nothing reads or writes
it any more. The model is kept only so it keeps matching the existing migrations (the
model-vs-migration drift check, and `flask db migrate`, must not propose dropping the table
or its data). Do not build on it.
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
