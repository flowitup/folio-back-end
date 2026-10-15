"""UpdateBillingDocumentStatusUseCase — transition a billing document's status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from app.application.billing._helpers import (
    _assert_billing_doc_write_access,
    _assert_devis_not_locked,
    _conversion_link,
    _funds_release_items,
    _release_allowed,
)
from app.application.billing.dtos import BillingDocumentResponse, UpdateStatusInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    FundsReleasePort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import BillingDocumentNotFoundError, StatusChangeNotSavedError
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
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
        project_repo: Optional[ProjectReadPort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._funds_release = funds_release
        self._access_repo = access_repo
        self._project_repo = project_repo

    def execute(
        self,
        inp: UpdateStatusInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # Lock the row so a concurrent change (PAID against CANCELLED) queues and then
        # validates against the status the winner saved, instead of both reading the same one.
        doc = self._doc_repo.find_by_id_for_update(inp.id)
        if doc is None:
            raise BillingDocumentNotFoundError(inp.id)
        _assert_billing_doc_write_access(doc, inp.user_id, self._access_repo)

        old_status = doc.status

        validate_status_transition(doc.kind, doc.status, inp.new_status)
        _assert_devis_not_locked(self._doc_repo, doc)

        updated = doc.with_updates(
            status=inp.new_status,
            updated_at=datetime.now(timezone.utc),
        )
        saved = self._doc_repo.save(updated)
        # Still under the row lock: the release follows the status just saved, and the
        # release adapter's commit makes both durable together.
        self._sync_funds_release(saved, old_status)
        db_session.commit()

        return BillingDocumentResponse.from_entity(saved, *_conversion_link(self._doc_repo, saved))

    def _sync_funds_release(
        self,
        doc: BillingDocument,
        old_status: BillingDocumentStatus,
    ) -> None:
        if self._funds_release is None:
            return
        if doc.kind != BillingDocumentKind.FACTURE:
            return

        if doc.status == BillingDocumentStatus.PAID:
            allowed = doc.project_id is not None and _release_allowed(self._project_repo, doc)
            if doc.project_id is not None and not allowed:
                log.warning("No funds release for facture %s: project %s is not its company's", doc.id, doc.project_id)
            self._funds_release.sync_funds_release(
                project_id=doc.project_id if allowed else None,
                source_doc_id=doc.id,
                amount_items=_funds_release_items(doc),
                recipient_name=doc.recipient_name,
                issue_date=doc.issue_date,
                created_by=doc.user_id,
            )
            log.info("Funds release synced for paid facture %s", doc.id)

        elif old_status == BillingDocumentStatus.PAID:
            self._funds_release.delete_funds_release(doc.id)
            log.info("Funds release deleted for facture %s, no longer paid", doc.id)

        else:
            return

        # A release insert rejected by a constraint rolls the whole session back (status
        # included) and the adapter swallows it: do not report a change the database lost.
        current = self._doc_repo.find_by_id(doc.id)
        if current is None or current.status != doc.status:
            raise StatusChangeNotSavedError(doc.id)
