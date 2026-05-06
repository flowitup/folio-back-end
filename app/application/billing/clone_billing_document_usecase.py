"""CloneBillingDocumentUseCase — clone an existing billing document into a new draft.

Phase 04 migration:
  - Accepts optional company_id from CloneBillingDocumentInput.
  - When company_id provided: validates attachment, snapshots from Company entity.
  - When company_id is None: falls back to source doc's company_id, then CompanyProfile.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.billing._helpers import (
    _assert_owner,
    _build_doc_from_inputs,
    _effective_prefix_from_company,
    _snapshot_issuer,
    _snapshot_issuer_from_company,
)
from app.application.billing.dtos import BillingDocumentResponse, CloneBillingDocumentInput
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
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import BillingDocumentNotFoundError, MissingCompanyProfileError
from app.domain.billing.numbering import next_document_number


class CloneBillingDocumentUseCase:
    """Clone an existing billing document into a new draft.

    Clone semantics (per spec):
      - Copies: items, recipient_*, notes, terms, signature_block_text.
      - Resets: id (new UUID), document_number (new atomic number), status=draft,
                issue_date=today, validity_until / payment_due_date recomputed.
      - Issuer snapshot taken from CURRENT company (or company_profile for legacy).
      - source_devis_id is NOT copied (plain clone, not a convert).
      - kind: use override_kind if supplied, else same kind as source.
      - company_id: use inp.company_id if supplied, else source doc's company_id.
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
        inp: CloneBillingDocumentInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. Load and authorise source document
        source = self._doc_repo.find_by_id(inp.source_id)
        if source is None:
            raise BillingDocumentNotFoundError(inp.source_id)
        _assert_owner(source, inp.user_id)

        # 2. Resolve effective company_id: prefer explicit override, then source doc's
        effective_company_id: UUID | None = inp.company_id if inp.company_id is not None else source.company_id

        # 3. Resolve issuer snapshot + counter key
        company = None
        if effective_company_id is not None:
            company = assert_user_company_access(
                self._access_repo, self._company_repo, inp.user_id, effective_company_id
            )

        if company is not None:
            issuer_snapshot = _snapshot_issuer_from_company(company)
            effective_prefix = _effective_prefix_from_company(company) or ""
            counter_key: UUID = effective_company_id  # type: ignore[assignment]
        else:
            # Legacy path: use CompanyProfile
            profile = self._profile_repo.find_by_user_id(inp.user_id) if self._profile_repo else None
            if profile is None:
                raise MissingCompanyProfileError(inp.user_id)
            issuer_snapshot = _snapshot_issuer(profile)
            effective_prefix = profile.effective_prefix
            counter_key = inp.user_id  # type: ignore[assignment]

        # 4. Determine target kind
        target_kind = inp.override_kind if inp.override_kind is not None else source.kind

        # H1: Verify project:read access if source doc has a project_id
        assert_project_read_access(self._project_repo, source.project_id, inp.user_id)

        # 5. Atomically generate new document number
        today = datetime.now(timezone.utc).date()
        sequence = self._counter_repo.next_value(counter_key, target_kind, today.year)
        document_number = next_document_number(
            prefix_override=effective_prefix,
            kind=target_kind,
            year=today.year,
            sequence=sequence,
        )

        # 6. Build new doc — dates reset, issuer from current company/profile.
        # H2: When changing kind, zero out incompatible fields to avoid DB CHECK constraint.
        source_payment_terms = source.payment_terms
        if target_kind == BillingDocumentKind.DEVIS and source.kind != BillingDocumentKind.DEVIS:
            source_payment_terms = None

        doc = _build_doc_from_inputs(
            user_id=inp.user_id,
            company_id=effective_company_id,
            kind=target_kind,
            document_number=document_number,
            issuer_snapshot=issuer_snapshot,
            recipient_name=source.recipient_name,
            items=source.items,
            issue_date=today,
            project_id=source.project_id,
            recipient_address=source.recipient_address,
            recipient_email=source.recipient_email,
            recipient_siret=source.recipient_siret,
            notes=source.notes,
            terms=source.terms,
            signature_block_text=source.signature_block_text,
            payment_terms=source_payment_terms,
            source_devis_id=None,  # plain clone — no audit trail link
        )

        saved = self._doc_repo.save(doc)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
