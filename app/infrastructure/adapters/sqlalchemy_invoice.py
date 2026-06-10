"""SQLAlchemy implementation of invoice repository."""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.invoice.ports import IInvoiceRepository
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import (
    InvoiceNotFoundError,
    InvoiceNumberConflictError,
)
from app.domain.value_objects.invoice_item import InvoiceItem
from app.infrastructure.database.models.invoice import InvoiceModel


def _items_to_jsonb(items: list) -> list:
    """Serialize InvoiceItem list to JSONB-compatible dicts (floats, not Decimals).

    Note: 'total' is intentionally omitted — it is always computed from
    quantity * unit_price at read time, so storing it wastes space and risks drift.
    """
    return [
        {
            "description": item.description,
            "quantity": float(item.quantity),
            "unit_price": float(item.unit_price),
        }
        for item in items
    ]


def _jsonb_to_items(raw: list) -> List[InvoiceItem]:
    """Deserialize JSONB dicts back to InvoiceItem value objects."""
    return [
        InvoiceItem(
            description=r["description"],
            quantity=Decimal(str(r["quantity"])),
            unit_price=Decimal(str(r["unit_price"])),
        )
        for r in (raw or [])
    ]


def _model_to_entity(m: InvoiceModel) -> Invoice:
    """Map ORM model to domain entity."""
    return Invoice(
        id=m.id,
        project_id=m.project_id,
        invoice_number=m.invoice_number,
        type=InvoiceType(m.type),
        issue_date=m.issue_date,
        recipient_name=m.recipient_name,
        recipient_address=m.recipient_address,
        notes=m.notes,
        items=_jsonb_to_items(m.items),
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
        payment_method_id=m.payment_method_id,
        payment_method_label=m.payment_method_label,
        source_billing_document_id=m.source_billing_document_id,
        is_auto_generated=m.is_auto_generated or False,
        tag_id=m.tag_id,
        refundable_status=m.refundable_status,
    )


class SQLAlchemyInvoiceRepository(IInvoiceRepository):
    """SQLAlchemy adapter for invoice persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, invoice: Invoice) -> Invoice:
        model = InvoiceModel(
            id=invoice.id,
            project_id=invoice.project_id,
            invoice_number=invoice.invoice_number,
            type=invoice.type.value,
            issue_date=invoice.issue_date,
            recipient_name=invoice.recipient_name,
            recipient_address=invoice.recipient_address,
            notes=invoice.notes,
            items=_items_to_jsonb(invoice.items),
            created_by=invoice.created_by,
            created_at=invoice.created_at,
            updated_at=invoice.updated_at,
            payment_method_id=invoice.payment_method_id,
            payment_method_label=invoice.payment_method_label,
            source_billing_document_id=invoice.source_billing_document_id,
            is_auto_generated=invoice.is_auto_generated,
            tag_id=invoice.tag_id,
            refundable_status=invoice.refundable_status,
        )
        self._session.add(model)
        try:
            self._session.commit()
        except IntegrityError as e:
            self._session.rollback()
            # Unique constraint on (project_id, invoice_number) was violated due
            # to a concurrent request generating the same sequential number.
            if "uq_project_invoice_number" in str(e.orig):
                raise InvoiceNumberConflictError("Invoice number conflict, please retry") from e
            raise
        return _model_to_entity(model)

    def find_by_id(self, invoice_id: UUID) -> Optional[Invoice]:
        model = self._session.query(InvoiceModel).filter_by(id=invoice_id).first()
        return _model_to_entity(model) if model else None

    def list_by_project(
        self,
        project_id: UUID,
        invoice_type: Optional[InvoiceType] = None,
        tag_id: Optional[UUID] = None,
    ) -> List[Invoice]:
        query = self._session.query(InvoiceModel).filter(InvoiceModel.project_id == project_id)
        if invoice_type is not None:
            query = query.filter(InvoiceModel.type == invoice_type.value)
        if tag_id is not None:
            query = query.filter(InvoiceModel.tag_id == tag_id)
        # Order by the invoice (issue) date, newest first; fall back to
        # creation time so same-date invoices keep a stable, deterministic order.
        models = query.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.created_at.desc()).all()
        return [_model_to_entity(m) for m in models]

    def update(self, invoice: Invoice) -> Invoice:
        model = self._session.query(InvoiceModel).filter_by(id=invoice.id).first()
        if not model:
            raise InvoiceNotFoundError(f"Invoice {invoice.id} not found")
        model.type = invoice.type.value
        model.recipient_name = invoice.recipient_name
        model.recipient_address = invoice.recipient_address
        model.issue_date = invoice.issue_date
        model.notes = invoice.notes
        model.items = _items_to_jsonb(invoice.items)
        model.updated_at = datetime.now(timezone.utc)
        model.payment_method_id = invoice.payment_method_id
        model.payment_method_label = invoice.payment_method_label
        model.tag_id = invoice.tag_id
        model.refundable_status = invoice.refundable_status
        self._session.commit()
        return _model_to_entity(model)

    def delete(self, invoice_id: UUID) -> bool:
        result = self._session.query(InvoiceModel).filter_by(id=invoice_id).delete()
        self._session.commit()
        return result > 0

    def find_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
        type_filter: Optional[InvoiceType] = None,
    ) -> List[Invoice]:
        """Return invoices where issue_date ∈ [date_from, date_to], optionally filtered by type.

        items is stored as a JSONB column (not a relationship), so there is no N+1 risk here.
        """
        q = (
            self._session.query(InvoiceModel)
            .filter(InvoiceModel.project_id == project_id)
            .filter(InvoiceModel.issue_date >= date_from)
            .filter(InvoiceModel.issue_date <= date_to)
        )
        if type_filter is not None:
            q = q.filter(InvoiceModel.type == type_filter.value)
        rows = q.order_by(InvoiceModel.issue_date, InvoiceModel.invoice_number).all()
        return [_model_to_entity(r) for r in rows]

    def next_funds_release_number(self, project_id: UUID) -> str:
        """Generate next sequential funds-release number: FR-YYYY-NNNN."""
        year = datetime.now(timezone.utc).year
        prefix = f"FR-{year}-"
        last = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.invoice_number.like(f"{prefix}%"),
            )
            .order_by(InvoiceModel.invoice_number.desc())
            .first()
        )
        n = 1
        if last:
            try:
                n = int(last.invoice_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{n:04d}"

    def delete_by_source_billing_document_id(self, source_doc_id: UUID) -> bool:
        result = (
            self._session.query(InvoiceModel)
            .filter(InvoiceModel.source_billing_document_id == source_doc_id)
            .delete(synchronize_session=False)
        )
        self._session.commit()
        return result > 0

    def sum_funds_released(self, project_id: UUID) -> Decimal:
        """Sum total_amount for all released_funds invoices in a project.

        items is JSONB — we compute in Python to stay DB-agnostic.
        """
        rows = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
            )
            .all()
        )
        total = Decimal("0")
        for m in rows:
            for it in m.items or []:
                total += Decimal(str(it.get("quantity", 0))) * Decimal(str(it.get("unit_price", 0)))
        return total

    def list_materials_services_by_companies(
        self,
        company_ids: list[UUID],
        refundable: Optional[bool],
        limit: int,
        offset: int,
        all_companies: bool = False,
    ) -> tuple[list[dict], int]:
        """Return paginated materials_services invoices across all projects of given companies.

        Single JOIN to projects avoids N+1 for project_name. Filters by company ownership
        and invoice type; optionally filters by refundable_status presence.

        all_companies=True bypasses the company_id filter (superadmin view).
        """
        from app.infrastructure.database.models.project import ProjectModel

        query = (
            self._session.query(InvoiceModel, ProjectModel.name.label("project_name"))
            .join(ProjectModel, InvoiceModel.project_id == ProjectModel.id)
            .filter(
                InvoiceModel.type == InvoiceType.MATERIALS_SERVICES.value,
                # Always require a company — projects with no company are excluded
                # from cross-company expense tracking regardless of caller scope.
                ProjectModel.company_id.isnot(None),
            )
        )

        if not all_companies:
            query = query.filter(ProjectModel.company_id.in_(company_ids))

        if refundable is True:
            query = query.filter(InvoiceModel.refundable_status.isnot(None))
        elif refundable is False:
            query = query.filter(InvoiceModel.refundable_status.is_(None))

        total: int = query.count()

        rows = (
            query.order_by(InvoiceModel.issue_date.desc(), InvoiceModel.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Collect invoice ids from this page so we can batch-load their attachments
        # in a single query, avoiding N+1 round-trips.
        page_invoice_ids = [inv_model.id for inv_model, _ in rows]

        # Build a {invoice_id -> [attachment dict, ...]} map. Newest-first with an
        # id tiebreaker — mirrors the per-invoice attachments endpoint so the same
        # invoice lists files in the same order on every surface.
        from app.infrastructure.database.models.invoice_attachment import InvoiceAttachmentModel

        attachments_by_invoice: dict = {}
        if page_invoice_ids:
            att_rows = (
                self._session.query(InvoiceAttachmentModel)
                .filter(InvoiceAttachmentModel.invoice_id.in_(page_invoice_ids))
                .order_by(
                    InvoiceAttachmentModel.uploaded_at.desc(),
                    InvoiceAttachmentModel.id.desc(),
                )
                .all()
            )
            for att in att_rows:
                key = att.invoice_id
                attachments_by_invoice.setdefault(key, []).append(
                    {
                        "id": str(att.id),
                        "filename": att.filename,
                        "mime_type": att.mime_type,
                        "size_bytes": att.size_bytes,
                    }
                )

        result = []
        for inv_model, project_name in rows:
            entity = _model_to_entity(inv_model)
            result.append(
                {
                    "id": str(entity.id),
                    "project_id": str(entity.project_id),
                    "project_name": project_name,
                    "invoice_number": entity.invoice_number,
                    "recipient_name": entity.recipient_name,
                    "issue_date": entity.issue_date.isoformat(),
                    "total_amount": float(entity.total_amount),
                    "refundable_status": entity.refundable_status,
                    "attachments": attachments_by_invoice.get(inv_model.id, []),
                }
            )
        return result, total

    def next_invoice_number(self, project_id: UUID) -> str:
        """Generate next sequential invoice number: PREFIX-YYYY-NNNN.

        Reads the project's custom invoice_prefix (falls back to "INV").
        """
        from app.infrastructure.database.models.project import ProjectModel

        project_row = self._session.query(ProjectModel.invoice_prefix).filter_by(id=project_id).first()
        tag = project_row[0] if project_row and project_row[0] else "INV"

        year = datetime.now(timezone.utc).year
        prefix = f"{tag}-{year}-"
        last = (
            self._session.query(InvoiceModel)
            .filter(
                InvoiceModel.project_id == project_id,
                InvoiceModel.invoice_number.like(f"{prefix}%"),
            )
            .order_by(InvoiceModel.invoice_number.desc())
            .first()
        )
        n = 1
        if last:
            try:
                n = int(last.invoice_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                pass
        return f"{prefix}{n:04d}"
