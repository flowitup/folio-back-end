"""Scheduled reminders already sent: one row per (user, reminder kind, Paris day).

The reminder job runs every 15 minutes and may run late or twice; the unique key is what
makes a send happen once. ``pushed`` tells a reminder that rang the phone from one that
only went to the bell (muted, or the day's one push was already used), so the per-day cap
counts only real pushes.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base


class ScheduledReminderLogModel(Base):
    __tablename__ = "scheduled_reminder_log"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "local_date", name="uq_scheduled_reminder_log_user_kind_date"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # The Europe/Paris calendar day the reminder belongs to.
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    pushed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
