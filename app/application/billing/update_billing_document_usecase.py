"""UpdateBillingDocumentUseCase — partial field update on a billing document."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import _assert_owner, _items_from_inputs
from app.application.billing.dtos import BillingDocumentResponse, UpdateBillingDocumentInput
from app.application.billing.ports import BillingDocumentRepositoryPort, TransactionalSessionPort
from app.domain.billing.exceptions import BillingDocumentNotFoundError


class UpdateBillingDocumentUseCase:
    """Partially update a billing document.

    Immutable fields (never changed by this use-case):
      kind, document_number, user_id, issuer_* snapshot fields, source_devis_id.

    Applies only fields that are explicitly set (not None) in the input DTO.
    """

    def __init__(self, doc_repo: BillingDocumentRepositoryPort) -> None:
        self._doc_repo = doc_repo

    def execute(
        self,
        inp: UpdateBillingDocumentInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        doc = self._doc_repo.find_by_id(inp.id)
        if doc is None:
            raise BillingDocumentNotFoundError(inp.id)
        _assert_owner(doc, inp.user_id)

        updates: dict = {"updated_at": datetime.now(timezone.utc)}

        if inp.recipient_name is not None:
            name = inp.recipient_name.strip()
            if not name:
                raise ValueError("Recipient name is required")
            updates["recipient_name"] = name

        if inp.recipient_address is not None:
            updates["recipient_address"] = inp.recipient_address

        if inp.recipient_email is not None:
            updates["recipient_email"] = inp.recipient_email

        if inp.recipient_siret is not None:
            updates["recipient_siret"] = inp.recipient_siret

        if inp.items is not None:
            if not inp.items:
                raise ValueError("At least one line item is required")
            updates["items"] = _items_from_inputs(inp.items)

        if inp.notes is not None:
            updates["notes"] = inp.notes

        if inp.terms is not None:
            updates["terms"] = inp.terms

        if inp.signature_block_text is not None:
            updates["signature_block_text"] = inp.signature_block_text

        if inp.validity_until is not None:
            updates["validity_until"] = inp.validity_until

        if inp.payment_due_date is not None:
            updates["payment_due_date"] = inp.payment_due_date

        if inp.payment_terms is not None:
            updates["payment_terms"] = inp.payment_terms

        if inp.project_id is not None:
            updates["project_id"] = inp.project_id

        if inp.issue_date is not None:
            updates["issue_date"] = inp.issue_date

        updated = doc.with_updates(**updates)
        saved = self._doc_repo.save(updated)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
