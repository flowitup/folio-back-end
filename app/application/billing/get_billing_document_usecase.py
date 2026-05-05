"""GetBillingDocumentUseCase — fetch a single billing document by ID."""

from __future__ import annotations

from uuid import UUID

from app.application.billing._helpers import _assert_owner
from app.application.billing.dtos import BillingDocumentResponse
from app.application.billing.ports import BillingDocumentRepositoryPort
from app.domain.billing.exceptions import BillingDocumentNotFoundError


class GetBillingDocumentUseCase:
    """Fetch a billing document by UUID with ownership check."""

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(self, doc_id: UUID, user_id: UUID) -> BillingDocumentResponse:
        doc = self._doc_repo.find_by_id(doc_id)
        if doc is None:
            raise BillingDocumentNotFoundError(doc_id)
        _assert_owner(doc, user_id)
        return BillingDocumentResponse.from_entity(doc)
