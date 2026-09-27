"""Date consistency rules for billing documents."""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.domain.billing.enums import BillingDocumentKind


def validate_document_dates(
    issue_date: date,
    validity_until: Optional[date] = None,
    payment_due_date: Optional[date] = None,
) -> None:
    """A devis cannot expire, nor a facture fall due, before it is issued.

    Raises ValueError (answered 400 by the routes).
    """
    if validity_until is not None and validity_until < issue_date:
        raise ValueError("validity_until cannot be before issue_date")
    if payment_due_date is not None and payment_due_date < issue_date:
        raise ValueError("payment_due_date cannot be before issue_date")


def validate_kind_fields(
    kind: BillingDocumentKind,
    validity_until: Optional[date] = None,
    payment_due_date: Optional[date] = None,
    payment_terms: Optional[str] = None,
) -> None:
    """Only a devis has a validity date; only a facture has payment fields.

    The database enforces the same rule with check constraints, so a
    mismatch must be refused here (ValueError, answered 400) before it is
    written.
    """
    if kind == BillingDocumentKind.DEVIS and (payment_due_date is not None or payment_terms is not None):
        raise ValueError("payment_due_date and payment_terms are only valid on facture documents")
    if kind == BillingDocumentKind.FACTURE and validity_until is not None:
        raise ValueError("validity_until is only valid on devis documents")
