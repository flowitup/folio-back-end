"""ListBillingDocumentsUseCase — paginated list of billing documents for a user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.billing.dtos import BillingDocumentResponse
from app.application.billing.ports import BillingDocumentRepositoryPort
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus


@dataclass(frozen=True)
class ListBillingDocumentsResult:
    """Paginated result envelope."""

    items: list[BillingDocumentResponse]
    total: int
    limit: int
    offset: int


class ListBillingDocumentsUseCase:
    """Return a paginated, filtered list of billing documents owned by the caller.

    Permission: caller must be the owner (user_id == row.user_id).
    The repository enforces the ownership filter via the user_id param.
    """

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ListBillingDocumentsResult:
        docs, total = self._doc_repo.list_for_user(
            user_id=user_id,
            kind=kind,
            status=status,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
        return ListBillingDocumentsResult(
            items=[BillingDocumentResponse.from_entity(d) for d in docs],
            total=total,
            limit=limit,
            offset=offset,
        )
