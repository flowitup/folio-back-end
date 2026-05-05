"""DeleteBillingDocumentUseCase — hard-delete a billing document."""

from __future__ import annotations

from uuid import UUID

from app.application.billing._helpers import _assert_owner
from app.application.billing.ports import BillingDocumentRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingDocumentNotFoundError


class DeleteBillingDocumentUseCase:
    """Hard-delete a billing document with ownership check."""

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(
        self,
        doc_id: UUID,
        user_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        doc = self._doc_repo.find_by_id(doc_id)
        if doc is None:
            raise BillingDocumentNotFoundError(doc_id)
        _assert_owner(doc, user_id)
        self._doc_repo.delete(doc_id)
        db_session.commit()
