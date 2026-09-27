"""Date consistency rules for billing documents."""

from __future__ import annotations

from datetime import date
from typing import Optional


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
