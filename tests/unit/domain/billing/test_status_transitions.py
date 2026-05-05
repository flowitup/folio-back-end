"""Unit tests for billing document status transition matrix.

Parametrised test_status_transition_matrix covers every cell of both
the devis and facture transition matrices.
"""

import pytest

from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import InvalidStatusTransitionError
from app.domain.billing.status import validate_status_transition

D = BillingDocumentKind.DEVIS
F = BillingDocumentKind.FACTURE

DRAFT = BillingDocumentStatus.DRAFT
SENT = BillingDocumentStatus.SENT
ACCEPTED = BillingDocumentStatus.ACCEPTED
REJECTED = BillingDocumentStatus.REJECTED
EXPIRED = BillingDocumentStatus.EXPIRED
PAID = BillingDocumentStatus.PAID
OVERDUE = BillingDocumentStatus.OVERDUE
CANCELLED = BillingDocumentStatus.CANCELLED


# fmt: off
# (kind, from_status, to_status, allowed)
MATRIX = [
    # ── DEVIS allowed transitions ──────────────────────────────────────────
    (D, DRAFT,    SENT,      True),
    (D, SENT,     ACCEPTED,  True),
    (D, SENT,     REJECTED,  True),
    (D, SENT,     EXPIRED,   True),
    (D, ACCEPTED, SENT,      True),   # revert
    (D, REJECTED, DRAFT,     True),   # re-open

    # ── DEVIS blocked transitions ──────────────────────────────────────────
    (D, DRAFT,    ACCEPTED,  False),
    (D, DRAFT,    REJECTED,  False),
    (D, DRAFT,    EXPIRED,   False),
    (D, DRAFT,    PAID,      False),
    (D, DRAFT,    OVERDUE,   False),
    (D, DRAFT,    CANCELLED, False),
    (D, SENT,     DRAFT,     False),
    (D, SENT,     PAID,      False),
    (D, SENT,     OVERDUE,   False),
    (D, SENT,     CANCELLED, False),
    (D, ACCEPTED, DRAFT,     False),
    (D, ACCEPTED, REJECTED,  False),
    (D, ACCEPTED, EXPIRED,   False),
    (D, ACCEPTED, PAID,      False),
    (D, ACCEPTED, OVERDUE,   False),
    (D, ACCEPTED, CANCELLED, False),
    (D, REJECTED, SENT,      False),
    (D, REJECTED, ACCEPTED,  False),
    (D, REJECTED, EXPIRED,   False),
    (D, REJECTED, PAID,      False),
    (D, REJECTED, OVERDUE,   False),
    (D, REJECTED, CANCELLED, False),
    (D, EXPIRED,  DRAFT,     False),
    (D, EXPIRED,  SENT,      False),
    (D, EXPIRED,  ACCEPTED,  False),
    (D, EXPIRED,  PAID,      False),

    # ── FACTURE allowed transitions ────────────────────────────────────────
    (F, DRAFT,    SENT,      True),
    (F, SENT,     PAID,      True),
    (F, SENT,     OVERDUE,   True),
    (F, SENT,     CANCELLED, True),
    (F, OVERDUE,  PAID,      True),
    (F, PAID,     CANCELLED, True),   # refund

    # ── FACTURE blocked transitions ────────────────────────────────────────
    (F, DRAFT,    ACCEPTED,  False),
    (F, DRAFT,    REJECTED,  False),
    (F, DRAFT,    EXPIRED,   False),
    (F, DRAFT,    PAID,      False),
    (F, DRAFT,    OVERDUE,   False),
    (F, DRAFT,    CANCELLED, False),
    (F, SENT,     DRAFT,     False),
    (F, SENT,     ACCEPTED,  False),
    (F, SENT,     REJECTED,  False),
    (F, SENT,     EXPIRED,   False),
    (F, OVERDUE,  SENT,      False),
    (F, OVERDUE,  CANCELLED, False),
    (F, PAID,     SENT,      False),
    (F, PAID,     OVERDUE,   False),
    (F, CANCELLED, DRAFT,    False),
    (F, CANCELLED, SENT,     False),
    (F, CANCELLED, PAID,     False),
]
# fmt: on


@pytest.mark.parametrize("kind,from_status,to_status,allowed", MATRIX)
def test_status_transition_matrix(kind, from_status, to_status, allowed):
    """Parametrised regression: every cell of the devis + facture matrix."""
    if allowed:
        # Should NOT raise
        validate_status_transition(kind, from_status, to_status)
    else:
        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            validate_status_transition(kind, from_status, to_status)
        err = exc_info.value
        assert err.kind == kind.value
        assert err.from_status == from_status.value
        assert err.to_status == to_status.value


def test_invalid_transition_error_message():
    """InvalidStatusTransitionError has informative string representation."""
    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        validate_status_transition(D, DRAFT, PAID)
    assert "devis" in str(exc_info.value)
    assert "draft" in str(exc_info.value)
    assert "paid" in str(exc_info.value)
