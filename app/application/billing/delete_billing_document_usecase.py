"""DeleteBillingDocumentUseCase — hard-delete a billing document."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing._helpers import _assert_billing_doc_access
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    FundsReleasePort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import BillingDocumentNotFoundError


class DeleteBillingDocumentUseCase:
    """Hard-delete a billing document. Owner or company-admin may delete.

    A paid facture linked to a project owns an auto-generated released_funds
    expense. It is removed before the facture, like the paid → cancelled
    transition does; otherwise the FK's ON DELETE SET NULL would leave a
    release that belongs to nothing and still counts in the project's funds.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
        funds_release: Optional[FundsReleasePort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._access_repo = access_repo
        self._funds_release = funds_release

    def execute(
        self,
        doc_id: UUID,
        user_id: UUID,
        db_session: TransactionalSessionPort,
    ) -> None:
        doc = self._doc_repo.find_by_id(doc_id)
        if doc is None:
            raise BillingDocumentNotFoundError(doc_id)
        _assert_billing_doc_access(doc, user_id, self._access_repo)
        if self._funds_release is not None and doc.kind == BillingDocumentKind.FACTURE:
            self._funds_release.delete_funds_release(doc.id)
        self._doc_repo.delete(doc_id)
        db_session.commit()
