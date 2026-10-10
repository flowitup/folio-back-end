"""UpdateBillingDocumentUseCase — partial field update on a billing document."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.application.billing._helpers import (
    _assert_billing_doc_write_access,
    _assert_devis_not_locked,
    _conversion_link,
    _funds_release_items,
    _items_from_inputs,
)
from app.application.billing.dtos import (
    CLEARABLE_BILLING_FIELDS,
    BillingDocumentResponse,
    UpdateBillingDocumentInput,
)
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    FundsReleasePort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
)
from app.domain.billing.dates import validate_document_dates, validate_kind_fields
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.numbering import document_number_year
from app.domain.billing.exceptions import BillingDocumentNotFoundError

_DATE_FIELDS = frozenset({"issue_date", "validity_until", "payment_due_date"})


def _check_dates(doc) -> None:
    """Validity/due dates stay on or after the issue date, which stays in the number's year."""
    validate_document_dates(doc.issue_date, doc.validity_until, doc.payment_due_date)
    number_year = document_number_year(doc.document_number)
    if number_year is not None and doc.issue_date.year != number_year:
        raise ValueError(f"issue_date must stay in {number_year}, the year of document number {doc.document_number}")


# Facture fields copied onto its released_funds expense.
_RELEASE_FIELDS = frozenset({"items", "recipient_name", "issue_date", "project_id"})


class UpdateBillingDocumentUseCase:
    """Partially update a billing document.

    Immutable fields (never changed by this use-case):
      kind, document_number, user_id, issuer_* snapshot fields, source_devis_id.

    Applies only fields that are explicitly set (not None) in the input DTO,
    and clears the optional fields named in `inp.cleared`.

    A paid facture's auto-generated released_funds expense mirrors its lines,
    recipient, issue date and project, so it is re-synced when any of them change.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        project_repo: ProjectReadPort = None,  # type: ignore[assignment]
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
        funds_release: Optional[FundsReleasePort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._project_repo = project_repo
        self._access_repo = access_repo
        self._funds_release = funds_release

    def execute(
        self,
        inp: UpdateBillingDocumentInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        doc = self._doc_repo.find_by_id(inp.id)
        if doc is None:
            raise BillingDocumentNotFoundError(inp.id)
        _assert_billing_doc_write_access(doc, inp.user_id, self._access_repo)
        _assert_devis_not_locked(self._doc_repo, doc)

        # M3: Reject kind-incompatible field updates before touching the DB.
        validate_kind_fields(doc.kind, inp.validity_until, inp.payment_due_date, inp.payment_terms)

        # H1: Verify project:read access when explicitly setting a new project_id.
        # update_project_id=True means the caller included the field; project_id may be
        # None to unlink, or a UUID to link. Only check access when linking (not None).
        if inp.update_project_id and inp.project_id is not None:
            assert_project_read_access(self._project_repo, inp.project_id, inp.user_id, self._access_repo)

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

        for name in inp.cleared & CLEARABLE_BILLING_FIELDS:
            updates[name] = None

        if inp.update_project_id:
            updates["project_id"] = inp.project_id  # may be None to unlink

        if inp.issue_date is not None:
            updates["issue_date"] = inp.issue_date

        updated = doc.with_updates(**updates)
        if _DATE_FIELDS.intersection(updates):
            _check_dates(updated)
        saved = self._doc_repo.save(updated)
        db_session.commit()

        if (
            self._funds_release is not None
            and saved.kind == BillingDocumentKind.FACTURE
            and saved.status == BillingDocumentStatus.PAID
            and _RELEASE_FIELDS.intersection(updates)
        ):
            self._funds_release.sync_funds_release(
                project_id=saved.project_id,
                source_doc_id=saved.id,
                amount_items=_funds_release_items(saved),
                recipient_name=saved.recipient_name,
                issue_date=saved.issue_date,
                created_by=saved.user_id,
            )

        return BillingDocumentResponse.from_entity(saved, *_conversion_link(self._doc_repo, saved))
