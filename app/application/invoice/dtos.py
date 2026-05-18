"""Shared response DTOs for the invoice application layer."""

from dataclasses import dataclass
from typing import Optional

from app.domain.entities.invoice import Invoice


@dataclass
class InvoiceItemResponse:
    description: str
    quantity: float
    unit_price: float
    total: float


@dataclass
class InvoiceResponse:
    id: str
    project_id: str
    invoice_number: str
    type: str
    issue_date: str
    recipient_name: str
    recipient_address: Optional[str]
    notes: Optional[str]
    items: list  # list[InvoiceItemResponse]
    total_amount: float
    created_by: str
    created_at: str
    updated_at: str
    payment_method_id: Optional[str] = None
    payment_method_label: Optional[str] = None
    source_billing_document_id: Optional[str] = None
    is_auto_generated: bool = False

    @classmethod
    def from_entity(cls, inv: Invoice) -> "InvoiceResponse":
        return cls(
            id=str(inv.id),
            project_id=str(inv.project_id),
            invoice_number=inv.invoice_number,
            type=inv.type.value,
            issue_date=inv.issue_date.isoformat(),
            recipient_name=inv.recipient_name,
            recipient_address=inv.recipient_address,
            notes=inv.notes,
            items=[
                InvoiceItemResponse(
                    description=item.description,
                    quantity=float(item.quantity),
                    unit_price=float(item.unit_price),
                    total=float(item.total),
                )
                for item in inv.items
            ],
            total_amount=float(inv.total_amount),
            created_by=str(inv.created_by),
            created_at=inv.created_at.isoformat(),
            updated_at=inv.updated_at.isoformat(),
            payment_method_id=str(inv.payment_method_id) if inv.payment_method_id is not None else None,
            payment_method_label=inv.payment_method_label,
            source_billing_document_id=(
                str(inv.source_billing_document_id) if inv.source_billing_document_id is not None else None
            ),
            is_auto_generated=inv.is_auto_generated,
        )
