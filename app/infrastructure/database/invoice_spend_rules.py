"""Shared money rules for invoice-derived spend figures.

Single source of truth for two questions every spend KPI has to answer the same way:

1. **How much is this invoice worth?** — ``items_total`` (TTC: quantity x unit_price x (1 + vat/100)).
2. **Was it funded with company money?** — ``is_company_paid``.

Both the Expense-page KPIs (``SqlAlchemyInvoiceRepository``) and the projects-list spend
breakdown (``SqlAlchemyProjectSpentReader``) go through here, so the two views can never
report different numbers for the same concept.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional
from uuid import UUID

from sqlalchemy.orm import Session


def items_total(items: Optional[list]) -> Decimal:
    """TTC total of a raw JSONB items list: sum of qty x unit_price x (1 + vat/100).

    Mirrors InvoiceItem.total / Invoice.total_amount exactly. Every Python-side aggregation
    over raw JSONB rows must go through this single helper so the math can never drift
    between KPIs. Legacy rows without 'vat_rate' default to 0 (no VAT).
    """
    total = Decimal("0")
    for it in items or []:
        qty = Decimal(str(it.get("quantity", 0)))
        price = Decimal(str(it.get("unit_price", 0)))
        vat = Decimal(str(it.get("vat_rate", 0)))
        total += qty * price * (1 + vat / Decimal("100"))
    return total


def load_company_paid_method_ids(session: Session, company_ids: Iterable[UUID]) -> dict[UUID, set[UUID]]:
    """Map company_id -> set of payment_method ids flagged ``is_company_payment``.

    ``is_active`` is deliberately ignored: deactivating a payment method must not erase
    spend that already happened. Companies with no flagged methods map to an empty set.
    Returns {} for empty input so callers never emit an invalid ``IN ()`` clause.
    """
    from app.infrastructure.database.models.payment_method import PaymentMethodModel

    ids = {cid for cid in company_ids if cid is not None}
    if not ids:
        return {}

    rows = (
        session.query(PaymentMethodModel.company_id, PaymentMethodModel.id)
        .filter(
            PaymentMethodModel.company_id.in_(ids),
            PaymentMethodModel.is_company_payment.is_(True),
        )
        .all()
    )

    result: dict[UUID, set[UUID]] = {cid: set() for cid in ids}
    for company_id, method_id in rows:
        result[company_id].add(method_id)
    return result


def is_company_paid(
    *,
    payment_method_id: Optional[UUID],
    refundable_status: Optional[str],
    refunded_by: Optional[str],
    company_paid_ids: set[UUID],
) -> bool:
    """Return True when the invoice was funded with company money.

    True when either:
      - the company reimbursed the expense (``refundable_status == 'refunded'`` and
        ``refunded_by != 'bank'``). A bank refund is the bank's money, not the company's.
        NULL ``refunded_by`` is legacy data and counts as company; 'both' counts too
        (the company did reimburse, the split is just unknown), OR
      - it was paid with a payment method flagged ``is_company_payment``.

    Callers pass raw column values, so this stays a pure predicate with no DB access and
    works identically against ORM instances and row tuples.
    """
    is_refunded = refundable_status == "refunded" and refunded_by != "bank"
    is_paid_by_company_method = payment_method_id is not None and payment_method_id in company_paid_ids
    return is_refunded or is_paid_by_company_method
