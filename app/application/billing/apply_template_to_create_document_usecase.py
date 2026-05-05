"""ApplyTemplateToCreateDocumentUseCase — create a document pre-filled from a template."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import (
    _build_doc_from_inputs,
    _snapshot_issuer,
)
from app.application.billing.dtos import ApplyTemplateInput, BillingDocumentResponse
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    BillingTemplateRepositoryPort,
    CompanyProfileRepositoryPort,
    TransactionalSessionPort,
)
from app.domain.billing.exceptions import (
    BillingTemplateNotFoundError,
    ForbiddenBillingDocumentError,
    MissingCompanyProfileError,
)
from app.domain.billing.numbering import next_document_number


class ApplyTemplateToCreateDocumentUseCase:
    """Create a new billing document pre-filled from a template.

    The template supplies: kind, items, notes, terms.
    The caller supplies: recipient_*, project_id, issue_date.
    The current company_profile supplies: issuer snapshot + payment_terms default.

    Validation:
      - Template must exist and be owned by user.
      - User must have a CompanyProfile.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        template_repo: BillingTemplateRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        profile_repo: CompanyProfileRepositoryPort,
    ) -> None:
        self._doc_repo = doc_repo
        self._template_repo = template_repo
        self._counter_repo = counter_repo
        self._profile_repo = profile_repo

    def execute(
        self,
        inp: ApplyTemplateInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. Load and authorise template
        template = self._template_repo.find_by_id(inp.template_id)
        if template is None:
            raise BillingTemplateNotFoundError(inp.template_id)
        if template.user_id != inp.user_id:
            raise ForbiddenBillingDocumentError(inp.template_id)

        # 2. Require company profile
        profile = self._profile_repo.find_by_user_id(inp.user_id)
        if profile is None:
            raise MissingCompanyProfileError(inp.user_id)
        issuer_snapshot = _snapshot_issuer(profile)

        # 3. Validate recipient name
        recipient_name = inp.recipient_name.strip() if inp.recipient_name else ""
        if not recipient_name:
            raise ValueError("Recipient name is required")

        # 4. Resolve issue_date and atomically generate document number
        today = datetime.now(timezone.utc).date()
        issue_date = inp.issue_date if inp.issue_date is not None else today
        sequence = self._counter_repo.next_value(inp.user_id, template.kind, issue_date.year)
        document_number = next_document_number(
            prefix_override=profile.effective_prefix,
            kind=template.kind,
            year=issue_date.year,
            sequence=sequence,
        )

        # 5. Resolve payment_terms from profile default (facture only)
        payment_terms = None
        if template.kind.value == "facture":
            payment_terms = profile.default_payment_terms

        # 6. Build document — items and notes/terms copied from template
        doc = _build_doc_from_inputs(
            user_id=inp.user_id,
            kind=template.kind,
            document_number=document_number,
            issuer_snapshot=issuer_snapshot,
            recipient_name=recipient_name,
            items=template.items,  # already tuple[BillingDocumentItem, ...]
            issue_date=issue_date,
            project_id=inp.project_id,
            recipient_address=inp.recipient_address,
            recipient_email=inp.recipient_email,
            recipient_siret=inp.recipient_siret,
            notes=template.notes,
            terms=template.terms,
            payment_terms=payment_terms,
            # validity_until / payment_due_date: None → defaults in _build_doc_from_inputs
        )

        saved = self._doc_repo.save(doc)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
