"""UpdateBillingDocumentStatusUseCase — transition a billing document's status."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import _assert_owner
from app.application.billing.dtos import BillingDocumentResponse, UpdateStatusInput
from app.application.billing.ports import BillingDocumentRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingDocumentNotFoundError
from app.domain.billing.status import validate_status_transition


class UpdateBillingDocumentStatusUseCase:
    """Transition a billing document to a new status.

    Delegates transition validation to the domain-level validate_status_transition
    helper, which enforces the per-kind transition matrix and raises
    InvalidStatusTransitionError on illegal moves.
    """

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(
        self,
        inp: UpdateStatusInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        doc = self._doc_repo.find_by_id(inp.id)
        if doc is None:
            raise BillingDocumentNotFoundError(inp.id)
        _assert_owner(doc, inp.user_id)

        # Raises InvalidStatusTransitionError if not allowed
        validate_status_transition(doc.kind, doc.status, inp.new_status)

        updated = doc.with_updates(
            status=inp.new_status,
            updated_at=datetime.now(timezone.utc),
        )
        saved = self._doc_repo.save(updated)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
