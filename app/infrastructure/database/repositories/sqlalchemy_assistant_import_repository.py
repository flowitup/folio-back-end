"""SQLAlchemy repository for the assistant's two AI-import extension tables.

Implements both ``InvoiceImportRepositoryPort`` and ``MaterialImportRepositoryPort`` in
one adapter — the two tables are unrelated but always used together by the same
``FeatureHandlers`` (feature C / feature A), and neither is large enough to warrant a
split file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.assistant.import_ports import (
    InvoiceImportRecord,
    MaterialImportRecord,
)
from app.infrastructure.database.models.assistant_imports import (
    AssistantMaterialImportModel,
    InvoiceAiImportModel,
)
from app.infrastructure.database.models.bibliotheque_product import BibliothequeProductModel


def _invoice_to_entity(m: InvoiceAiImportModel) -> InvoiceImportRecord:
    return InvoiceImportRecord(
        id=m.id,
        invoice_id=m.invoice_id,
        status=m.status,
        source=m.source,
        ai_confidence=float(m.ai_confidence),
        category=m.category,
        flags=list(m.flags) if m.flags else [],
        original_attachment_id=m.original_attachment_id,
        scan_attachment_id=m.scan_attachment_id,
        trace_id=m.trace_id,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _material_to_entity(m: AssistantMaterialImportModel) -> MaterialImportRecord:
    return MaterialImportRecord(
        id=m.id,
        product_id=m.product_id,
        company_id=m.company_id,
        status=m.status,
        confidence=float(m.confidence),
        photo_sha256=m.photo_sha256,
        source_url=m.source_url,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SqlAlchemyAssistantImportRepository:
    """Implements ``InvoiceImportRepositoryPort`` and ``MaterialImportRepositoryPort``."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # invoice_ai_imports
    # ------------------------------------------------------------------

    def add_invoice_import(
        self,
        *,
        invoice_id: UUID,
        status: str,
        source: str,
        ai_confidence: float,
        category: Optional[str] = None,
        flags: Optional[list[str]] = None,
        original_attachment_id: Optional[UUID] = None,
        scan_attachment_id: Optional[UUID] = None,
        trace_id: Optional[str] = None,
    ) -> InvoiceImportRecord:
        now = datetime.now(timezone.utc)
        model = InvoiceAiImportModel(
            id=uuid4(),
            invoice_id=invoice_id,
            status=status,
            source=source,
            ai_confidence=ai_confidence,
            category=category,
            flags=list(flags) if flags else None,
            original_attachment_id=original_attachment_id,
            scan_attachment_id=scan_attachment_id,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(model)
        self._session.commit()
        return _invoice_to_entity(model)

    def find_by_invoice(self, invoice_id: UUID) -> Optional[InvoiceImportRecord]:
        model = (
            self._session.execute(
                select(InvoiceAiImportModel)
                .where(InvoiceAiImportModel.invoice_id == invoice_id)
                .order_by(InvoiceAiImportModel.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _invoice_to_entity(model) if model is not None else None

    def list_without_scan_for_invoices(self, invoice_ids: list[UUID]) -> list[UUID]:
        if not invoice_ids:
            return []
        # Latest import row per invoice_id (subquery keyed on max created_at), then keep
        # ids whose latest row has no scan yet, or that never had an import row at all.
        latest_created = (
            select(
                InvoiceAiImportModel.invoice_id,
                func.max(InvoiceAiImportModel.created_at).label("latest_created_at"),
            )
            .where(InvoiceAiImportModel.invoice_id.in_(invoice_ids))
            .group_by(InvoiceAiImportModel.invoice_id)
            .subquery()
        )
        rows = self._session.execute(
            select(InvoiceAiImportModel.invoice_id, InvoiceAiImportModel.scan_attachment_id).join(
                latest_created,
                (InvoiceAiImportModel.invoice_id == latest_created.c.invoice_id)
                & (InvoiceAiImportModel.created_at == latest_created.c.latest_created_at),
            )
        ).all()
        with_scan = {invoice_id for invoice_id, scan_attachment_id in rows if scan_attachment_id is not None}
        return [invoice_id for invoice_id in invoice_ids if invoice_id not in with_scan]

    def update_invoice_status(self, import_id: UUID, status: str, flags: Optional[list[str]] = None) -> None:
        model = self._session.get(InvoiceAiImportModel, import_id)
        if model is None:
            return
        model.status = status
        if flags is not None:
            model.flags = list(flags)
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()

    # ------------------------------------------------------------------
    # assistant_material_imports
    # ------------------------------------------------------------------

    def add_material_import(
        self,
        *,
        product_id: UUID,
        company_id: UUID,
        status: str,
        confidence: float,
        photo_sha256: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> MaterialImportRecord:
        now = datetime.now(timezone.utc)
        model = AssistantMaterialImportModel(
            id=uuid4(),
            product_id=product_id,
            company_id=company_id,
            status=status,
            confidence=confidence,
            photo_sha256=photo_sha256 or uuid4().hex,
            source_url=source_url,
            created_at=now,
            updated_at=now,
        )
        self._session.add(model)
        self._session.commit()
        return _material_to_entity(model)

    def find_by_photo_hash(self, company_id: UUID, photo_sha256: str) -> Optional[MaterialImportRecord]:
        model = self._session.execute(
            select(AssistantMaterialImportModel).where(
                AssistantMaterialImportModel.company_id == company_id,
                AssistantMaterialImportModel.photo_sha256 == photo_sha256,
            )
        ).scalar_one_or_none()
        return _material_to_entity(model) if model is not None else None

    def find_by_reference(self, company_id: UUID, reference: str) -> Optional[MaterialImportRecord]:
        model = (
            self._session.execute(
                select(AssistantMaterialImportModel)
                .join(BibliothequeProductModel, BibliothequeProductModel.id == AssistantMaterialImportModel.product_id)
                .where(
                    BibliothequeProductModel.company_id == company_id,
                    func.lower(BibliothequeProductModel.supplier_reference) == reference.strip().lower(),
                )
                .order_by(AssistantMaterialImportModel.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _material_to_entity(model) if model is not None else None

    def update_material_status(self, import_id: UUID, status: str) -> None:
        model = self._session.get(AssistantMaterialImportModel, import_id)
        if model is None:
            return
        model.status = status
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()
