"""CreateBillingDocumentUseCase — create a new billing document from scratch.

Phase 04 migration:
  - Accepts company_id from CreateBillingDocumentInput.
  - When company_id is provided: validates user attachment via UserCompanyAccessRepository,
    snapshots issuer fields from Company entity, keys counter by company_id.
  - When company_id is None (legacy path): falls back to CompanyProfileRepository
    (kept for backward-compat during test migration in phase 05).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.billing._helpers import (
    _build_doc_from_inputs,
    _effective_prefix_from_company,
    _items_from_inputs,
    _snapshot_issuer,
    _snapshot_issuer_from_company,
)
from app.application.billing.dtos import BillingDocumentResponse, CreateBillingDocumentInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    CompanyProfileRepositoryPort,
    CompanyRepositoryPort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
    assert_user_company_access,
)
from app.domain.billing.exceptions import MissingCompanyProfileError
from app.domain.billing.numbering import next_document_number


class CreateBillingDocumentUseCase:
    """Create a new billing document.

    Pre-conditions:
      - When company_id provided: user must be attached to that company.
      - When company_id is None (legacy): user must have a CompanyProfile.
      - At least one line item required.
      - Number generated atomically via counter repo (SELECT FOR UPDATE).

    The entire operation runs inside the caller-supplied db_session transaction.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        profile_repo: CompanyProfileRepositoryPort,
        project_repo: ProjectReadPort = None,  # type: ignore[assignment]
        company_repo: CompanyRepositoryPort = None,  # type: ignore[assignment]
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
    ) -> None:
        self._doc_repo = doc_repo
        self._counter_repo = counter_repo
        self._profile_repo = profile_repo
        self._project_repo = project_repo
        self._company_repo = company_repo
        self._access_repo = access_repo

    def execute(
        self,
        inp: CreateBillingDocumentInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. Verify project:read access if project_id supplied (H1 — auth boundary)
        assert_project_read_access(self._project_repo, inp.project_id, inp.user_id)

        # 2. Resolve issuer snapshot + counter key + default_payment_terms
        default_payment_terms = None
        if inp.company_id is not None:
            # Phase 04+ path: validate attachment and snapshot from Company entity
            company = assert_user_company_access(self._access_repo, self._company_repo, inp.user_id, inp.company_id)
            if company is None:
                # company_id supplied but repos not wired — treat as legacy path
                profile = self._profile_repo.find_by_user_id(inp.user_id) if self._profile_repo else None
                if profile is None:
                    raise MissingCompanyProfileError(inp.user_id)
                issuer_snapshot = _snapshot_issuer(profile)
                effective_prefix = profile.effective_prefix
                counter_key: UUID = inp.user_id  # type: ignore[assignment]
                default_payment_terms = profile.default_payment_terms
            else:
                issuer_snapshot = _snapshot_issuer_from_company(company)
                effective_prefix = _effective_prefix_from_company(company) or ""
                counter_key = inp.company_id
                default_payment_terms = company.default_payment_terms
        else:
            # Legacy path: use CompanyProfile
            profile = self._profile_repo.find_by_user_id(inp.user_id) if self._profile_repo else None
            if profile is None:
                raise MissingCompanyProfileError(inp.user_id)
            issuer_snapshot = _snapshot_issuer(profile)
            effective_prefix = profile.effective_prefix
            counter_key = inp.user_id  # type: ignore[assignment]
            default_payment_terms = profile.default_payment_terms

        # 3. Validate + convert items
        if not inp.items:
            raise ValueError("At least one line item is required")
        items = _items_from_inputs(inp.items)

        # 4. Validate recipient name
        recipient_name = inp.recipient_name.strip() if inp.recipient_name else ""
        if not recipient_name:
            raise ValueError("Recipient name is required")

        # 5. Atomically generate document number
        issue_date = inp.issue_date if inp.issue_date is not None else datetime.now(timezone.utc).date()
        year = issue_date.year
        sequence = self._counter_repo.next_value(counter_key, inp.kind, year)
        document_number = next_document_number(
            prefix_override=effective_prefix,
            kind=inp.kind,
            year=year,
            sequence=sequence,
        )

        # 6. Resolve payment_terms: use input value or fall back to company/profile default
        payment_terms = inp.payment_terms
        if payment_terms is None and inp.kind.value == "facture":
            if inp.company_id is not None and company is not None:
                payment_terms = company.default_payment_terms
            elif "profile" in dir() and profile is not None:  # type: ignore[possibly-undefined]
                payment_terms = profile.default_payment_terms  # type: ignore[possibly-undefined]

        # 7. Build and persist document
        doc = _build_doc_from_inputs(
            user_id=inp.user_id,
            company_id=inp.company_id,
            kind=inp.kind,
            document_number=document_number,
            issuer_snapshot=issuer_snapshot,
            recipient_name=recipient_name,
            items=items,
            issue_date=inp.issue_date,
            project_id=inp.project_id,
            recipient_address=inp.recipient_address,
            recipient_email=inp.recipient_email,
            recipient_siret=inp.recipient_siret,
            notes=inp.notes,
            terms=inp.terms,
            signature_block_text=inp.signature_block_text,
            validity_until=inp.validity_until,
            payment_due_date=inp.payment_due_date,
            payment_terms=payment_terms,
        )

        saved = self._doc_repo.save(doc)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
