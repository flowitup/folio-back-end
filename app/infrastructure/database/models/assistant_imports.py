"""ORM models for the assistant's two AI-import extension tables (phase 03).

``invoice_ai_imports`` and ``assistant_material_imports`` never carry the numbers or
metadata a route/report reads directly — they only record what the assistant did to an
``invoices`` / ``library_products`` row, so a human (or a later pass) can audit or
re-triage it. See ``app.application.assistant.import_ports`` for the port contracts and
``SqlAlchemyAssistantImportRepository`` for the repository implementing both.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.models.base import Base

# JSONB on Postgres, generic JSON elsewhere (SQLite in tests) — same pattern as
# chat_messages.payload.
FlagsJSON = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InvoiceAiImportModel(Base):
    """One assistant import/attach event for an invoice (feature C, feature B)."""

    __tablename__ = "invoice_ai_imports"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    flags: Mapped[Optional[list[Any]]] = mapped_column(FlagsJSON, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    ai_confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    original_attachment_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("invoice_attachments.id", ondelete="SET NULL"), nullable=True
    )
    scan_attachment_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("invoice_attachments.id", ondelete="SET NULL"), nullable=True
    )
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class AssistantMaterialImportModel(Base):
    """One assistant import event for a library product (feature A).

    ``company_id`` is denormalised from ``bibliotheque_products.company_id`` at write
    time so the photo-hash cache (``photo_sha256``) can be a real per-company DB
    constraint — two companies photographing the same product are two independent
    imports, never a cross-tenant cache hit (see the module docstring's own migration
    note and review finding H4).
    """

    __tablename__ = "assistant_material_imports"
    __table_args__ = (UniqueConstraint("company_id", "photo_sha256"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("bibliotheque_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    photo_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
