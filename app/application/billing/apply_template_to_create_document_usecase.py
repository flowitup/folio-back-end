"""ApplyTemplateToCreateDocumentUseCase — create a document pre-filled from a template.

Phase 05 tightening:
  - company_id is now REQUIRED in ApplyTemplateInput.
  - Legacy CompanyProfile fallback removed.
  - CompanyProfileRepositoryPort still accepted for wiring compat but unused.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.billing._helpers import (
    _build_doc_from_inputs,
    _effective_prefix_from_company,
    _snapshot_issuer_from_company,
)
from app.application.billing.dtos import ApplyTemplateInput, BillingDocumentResponse
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    BillingTemplateRepositoryPort,
    CompanyProfileRepositoryPort,
    CompanyRepositoryPort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
    assert_user_company_access,
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
    The current company supplies: issuer snapshot + payment_terms default.

    Validation:
      - Template must exist and be owned by user.
      - company_id is required; user must be attached to that company.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        template_repo: BillingTemplateRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        profile_repo: CompanyProfileRepositoryPort,  # kept for wiring compat, unused
        project_repo: ProjectReadPort = None,  # type: ignore[assignment]
        company_repo: CompanyRepositoryPort = None,  # type: ignore[assignment]
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
    ) -> None:
        self._doc_repo = doc_repo
        self._template_repo = template_repo
        self._counter_repo = counter_repo
        self._profile_repo = profile_repo  # no longer used — kept to avoid wiring drift
        self._project_repo = project_repo
        self._company_repo = company_repo
        self._access_repo = access_repo

    def execute(
        self,
        inp: ApplyTemplateInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. Verify project:read access if project_id supplied (H1 — auth boundary)
        assert_project_read_access(self._project_repo, inp.project_id, inp.user_id)

        # 2. Load and authorise template
        template = self._template_repo.find_by_id(inp.template_id)
        if template is None:
            raise BillingTemplateNotFoundError(inp.template_id)
        if template.user_id != inp.user_id:
            raise ForbiddenBillingDocumentError(inp.template_id)

        # 3. company_id is required — validate attachment and snapshot from Company entity
        if inp.company_id is None:
            raise MissingCompanyProfileError(inp.user_id)

        company = assert_user_company_access(self._access_repo, self._company_repo, inp.user_id, inp.company_id)
        if company is None:
            raise MissingCompanyProfileError(inp.user_id)

        issuer_snapshot = _snapshot_issuer_from_company(company)
        effective_prefix = _effective_prefix_from_company(company) or ""
        counter_key: UUID = inp.company_id
        default_payment_terms = company.default_payment_terms

        # 4. Validate recipient name
        recipient_name = inp.recipient_name.strip() if inp.recipient_name else ""
        if not recipient_name:
            raise ValueError("Recipient name is required")

        # 5. Resolve issue_date and atomically generate document number
        today = datetime.now(timezone.utc).date()
        issue_date = inp.issue_date if inp.issue_date is not None else today
        sequence = self._counter_repo.next_value(counter_key, template.kind, issue_date.year)
        document_number = next_document_number(
            prefix_override=effective_prefix,
            kind=template.kind,
            year=issue_date.year,
            sequence=sequence,
        )

        # 6. Resolve payment_terms from company default (facture only)
        payment_terms = None
        if template.kind.value == "facture":
            payment_terms = default_payment_terms

        # 7. Build document — items and notes/terms copied from template
        doc = _build_doc_from_inputs(
            user_id=inp.user_id,
            company_id=inp.company_id,
            kind=template.kind,
            document_number=document_number,
            issuer_snapshot=issuer_snapshot,
            recipient_name=recipient_name,
            items=template.items,
            issue_date=issue_date,
            project_id=inp.project_id,
            recipient_address=inp.recipient_address,
            recipient_email=inp.recipient_email,
            recipient_siret=inp.recipient_siret,
            notes=template.notes,
            terms=template.terms,
            payment_terms=payment_terms,
        )

        saved = self._doc_repo.save(doc)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
