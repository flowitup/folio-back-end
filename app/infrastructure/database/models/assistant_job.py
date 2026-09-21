"""ORM model for ``assistant_jobs`` (feature B — invoice fetch via the browser worker).

Polled directly with plain SQL by the ``ai-browser`` container (``app.infrastructure.
browser_worker``), which never boots the Flask app — see that package's docstring for
why. This module (and the migration) is the single source of truth for the table shape
both that container and ``SqlAlchemyAssistantJobRepository`` agree on.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base

# JSONB on Postgres, generic JSON elsewhere (SQLite in tests) — same pattern as
# invoice_ai_imports.flags / chat_messages.payload.
ResultJSON = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssistantJobModel(Base):
    """One ``fetch_invoice`` or ``find_product`` job (see ``jobs_repo.JOB_TYPES``)."""

    __tablename__ = "assistant_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default="fetch_invoice")
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # fetch_invoice-only (always NULL for find_product, which uses `params` instead).
    merchant: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    amount_ttc: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    date: Mapped[Optional[date]] = mapped_column(Date(), nullable=True)
    project_hint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(ResultJSON, nullable=True)
    pdf_storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    status_message_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True
    )
    # The requester's language (vi|fr|en), so the browser worker container — which has
    # no chat-repository/messenger wiring to look up the original message's `lang`
    # payload — can still render the transient "running" job_status text correctly
    # instead of always French (review of phase 04, unresolved question 3).
    lang: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    # find_product-only: the MaterialIdent dump, search_queries, company_id,
    # photo_sha256 and message_id (see app.application.assistant.features.material) —
    # this job type has no fixed merchant/amount/date to key on, so everything the
    # browser worker and on_result need travels here instead. Always NULL for
    # fetch_invoice.
    params: Mapped[Optional[dict[str, Any]]] = mapped_column(ResultJSON, nullable=True)
    # Set exactly once, atomically, right before `on_result`'s "done" branch runs the
    # create-invoice pipeline — guards against a duplicate invoice if
    # `process_fetched_invoice` is ever invoked twice for the same job (an RQ retry
    # policy, a manual requeue, a double enqueue after a worker crash between
    # `update_result` and `enqueue`; review finding H3).
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
