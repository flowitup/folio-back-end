"""Status transition validation for billing documents.

validate_status_transition(kind, from_status, to_status) -> None
    Raises InvalidStatusTransitionError when the transition is not in the
    allowed matrix for the given document kind.

Transition matrix per brainstorm §"Status transition matrix":

  devis:   draft → sent
           sent  → accepted | rejected | expired
           accepted → sent  (revert)
           rejected → draft (re-open)

  facture: draft   → sent
           sent    → paid | overdue | cancelled
           overdue → paid
           paid    → cancelled  (refund)
"""

from __future__ import annotations

from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import InvalidStatusTransitionError

# Each entry maps from_status → frozenset of allowed to_status values.
_DEVIS_TRANSITIONS: dict[BillingDocumentStatus, frozenset[BillingDocumentStatus]] = {
    BillingDocumentStatus.DRAFT: frozenset({BillingDocumentStatus.SENT}),
    BillingDocumentStatus.SENT: frozenset(
        {
            BillingDocumentStatus.ACCEPTED,
            BillingDocumentStatus.REJECTED,
            BillingDocumentStatus.EXPIRED,
        }
    ),
    BillingDocumentStatus.ACCEPTED: frozenset({BillingDocumentStatus.SENT}),
    BillingDocumentStatus.REJECTED: frozenset({BillingDocumentStatus.DRAFT}),
    BillingDocumentStatus.EXPIRED: frozenset(),
    BillingDocumentStatus.PAID: frozenset(),
    BillingDocumentStatus.OVERDUE: frozenset(),
    BillingDocumentStatus.CANCELLED: frozenset(),
}

_FACTURE_TRANSITIONS: dict[BillingDocumentStatus, frozenset[BillingDocumentStatus]] = {
    BillingDocumentStatus.DRAFT: frozenset({BillingDocumentStatus.SENT}),
    BillingDocumentStatus.SENT: frozenset(
        {
            BillingDocumentStatus.PAID,
            BillingDocumentStatus.OVERDUE,
            BillingDocumentStatus.CANCELLED,
        }
    ),
    BillingDocumentStatus.OVERDUE: frozenset({BillingDocumentStatus.PAID}),
    BillingDocumentStatus.PAID: frozenset({BillingDocumentStatus.CANCELLED}),
    BillingDocumentStatus.ACCEPTED: frozenset(),
    BillingDocumentStatus.REJECTED: frozenset(),
    BillingDocumentStatus.EXPIRED: frozenset(),
    BillingDocumentStatus.CANCELLED: frozenset(),
}

_TRANSITION_MATRIX: dict[
    BillingDocumentKind,
    dict[BillingDocumentStatus, frozenset[BillingDocumentStatus]],
] = {
    BillingDocumentKind.DEVIS: _DEVIS_TRANSITIONS,
    BillingDocumentKind.FACTURE: _FACTURE_TRANSITIONS,
}


def validate_status_transition(
    kind: BillingDocumentKind,
    from_status: BillingDocumentStatus,
    to_status: BillingDocumentStatus,
) -> None:
    """Assert that transitioning from_status → to_status is allowed for kind.

    Raises:
        InvalidStatusTransitionError: when the transition is not in the matrix.
    """
    allowed = _TRANSITION_MATRIX[kind].get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidStatusTransitionError(
            kind=kind.value,
            from_status=from_status.value,
            to_status=to_status.value,
        )
