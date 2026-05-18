"""UpdateBillingDocumentStatusUseCase — transition a billing document's status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.application.billing._helpers import _assert_owner
from app.application.billing.dtos import BillingDocumentResponse, UpdateStatusInput
from app.application.billing.ports import BillingDocumentRepositoryPort, FundsReleasePort, TransactionalSessionPort
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import BillingDocumentNotFoundError
from app.domain.billing.status import validate_status_transition

if TYPE_CHECKING:
    from app.domain.billing.document import BillingDocument

log = logging.getLogger(__name__)


class UpdateBillingDocumentStatusUseCase:
    """Transition a billing document to a new status.

    Delegates transition validation to the domain-level validate_status_transition
    helper, which enforces the per-kind transition matrix and raises
    InvalidStatusTransitionError on illegal moves.

    When a facture transitions to PAID and has a project_id, a released_funds
    invoice is auto-created in the project's expenses. When PAID → CANCELLED,
    the linked funds release is auto-deleted.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        funds_release: Optional[FundsReleasePort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._funds_release = funds_release

    def execute(
        self,
        inp: UpdateStatusInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        doc = self._doc_repo.find_by_id(inp.id)
        if doc is None:
            raise BillingDocumentNotFoundError(inp.id)
        _assert_owner(doc, inp.user_id)

        old_status = doc.status

        validate_status_transition(doc.kind, doc.status, inp.new_status)

        updated = doc.with_updates(
            status=inp.new_status,
            updated_at=datetime.now(timezone.utc),
        )
        saved = self._doc_repo.save(updated)
        db_session.commit()

        self._handle_funds_release(saved, old_status, inp.new_status)

        return BillingDocumentResponse.from_entity(saved)

    def _handle_funds_release(
        self,
        doc: BillingDocument,
        old_status: BillingDocumentStatus,
        new_status: BillingDocumentStatus,
    ) -> None:
        if self._funds_release is None:
            return
        if doc.kind != BillingDocumentKind.FACTURE:
            return
        if doc.project_id is None:
            return

        if new_status == BillingDocumentStatus.PAID:
            items_dicts = [
                {
                    "description": it.description,
                    "quantity": str(it.quantity),
                    "unit_price": str(it.unit_price),
                }
                for it in doc.items
            ]
            self._funds_release.create_funds_release(
                project_id=doc.project_id,
                source_doc_id=doc.id,
                amount_items=items_dicts,
                recipient_name=doc.recipient_name,
                issue_date=doc.issue_date,
                created_by=doc.user_id,
            )
            log.info("Funds release created for facture %s in project %s", doc.id, doc.project_id)

        elif old_status == BillingDocumentStatus.PAID and new_status == BillingDocumentStatus.CANCELLED:
            self._funds_release.delete_funds_release(doc.id)
            log.info("Funds release deleted for cancelled facture %s", doc.id)
