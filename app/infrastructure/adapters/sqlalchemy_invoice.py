"""SQLAlchemy implementation of invoice repository."""

from datetime import datetime, timezone
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

    def list_by_project(self, project_id: UUID, invoice_type: Optional[InvoiceType] = None) -> List[Invoice]:
        query = self._session.query(InvoiceModel).filter(InvoiceModel.project_id == project_id)
        if invoice_type is not None:
            query = query.filter(InvoiceModel.type == invoice_type.value)
        models = query.order_by(InvoiceModel.created_at.desc()).all()
        return [_model_to_entity(m) for m in models]

    def update(self, invoice: Invoice) -> Invoice:
        model = self._session.query(InvoiceModel).filter_by(id=invoice.id).first()
        if not model:
            raise InvoiceNotFoundError(f"Invoice {invoice.id} not found")
        model.recipient_name = invoice.recipient_name
        model.recipient_address = invoice.recipient_address
        model.issue_date = invoice.issue_date
        model.notes = invoice.notes
        model.items = _items_to_jsonb(invoice.items)
        model.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        return _model_to_entity(model)

    def delete(self, invoice_id: UUID) -> bool:
        result = self._session.query(InvoiceModel).filter_by(id=invoice_id).delete()
        self._session.commit()
        return result > 0

    def next_invoice_number(self, project_id: UUID) -> str:
        """Generate next sequential invoice number: INV-YYYY-NNNN."""
        year = datetime.now(timezone.utc).year
        prefix = f"INV-{year}-"
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
