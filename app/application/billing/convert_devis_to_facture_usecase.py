"""ConvertDevisToFactureUseCase — convert an accepted devis into a new facture draft.

Phase 05 tightening:
  - company_id must always resolve to a non-None value after the convert logic.
  - Legacy CompanyProfile fallback removed.
  - If no explicit company_id and source doc has none, raises MissingCompanyProfileError.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.billing._helpers import (
    _assert_owner,
    _build_doc_from_inputs,
    _effective_prefix_from_company,
    _snapshot_issuer_from_company,
)
from app.application.billing.dtos import BillingDocumentResponse, ConvertDevisToFactureInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    CompanyRepositoryPort,
    ProjectReadPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
    assert_user_company_access,
)
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import (
    BillingDocumentNotFoundError,
    DevisAlreadyConvertedError,
    MissingCompanyProfileError,
)
from app.domain.billing.numbering import next_document_number


class ConvertDevisToFactureUseCase:
    """Convert an accepted devis into a new facture (status=draft).

    Concurrency safety:
      - Source devis is loaded via find_by_id_for_update (SELECT FOR UPDATE).
      - find_by_source_devis_id check performed inside the same transaction.
      - DB unique constraint on (source_devis_id) is the final backstop.

    Pre-conditions:
      - source_devis must exist, be owned by user, kind=DEVIS, status=ACCEPTED.
      - No existing facture linked to source_devis_id (raises DevisAlreadyConvertedError).
      - company_id must resolve to non-None; user must be attached to that company.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        project_repo: ProjectReadPort,
        company_repo: CompanyRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
    ) -> None:
        self._doc_repo = doc_repo
        self._counter_repo = counter_repo
        self._project_repo = project_repo
        self._company_repo = company_repo
        self._access_repo = access_repo

    def execute(
        self,
        inp: ConvertDevisToFactureInput,
        db_session: TransactionalSessionPort,
    ) -> BillingDocumentResponse:
        # 1. Lock source devis row to serialise concurrent converts
        source = self._doc_repo.find_by_id_for_update(inp.source_devis_id)
        if source is None:
            raise BillingDocumentNotFoundError(inp.source_devis_id)
        _assert_owner(source, inp.user_id)

        # 2. Assert kind and status preconditions
        if source.kind != BillingDocumentKind.DEVIS:
            raise ValueError(f"Document {source.id} is not a devis (kind={source.kind.value})")
        if source.status != BillingDocumentStatus.ACCEPTED:
            raise ValueError(
                f"Devis {source.id} must be in 'accepted' status to convert " f"(current: {source.status.value})"
            )

        # 3. Race guard — abort if conversion already happened
        existing = self._doc_repo.find_by_source_devis_id(inp.source_devis_id)
        if existing is not None:
            raise DevisAlreadyConvertedError(inp.source_devis_id)

        # H1: Verify project:read access if source doc has a project_id
        assert_project_read_access(self._project_repo, source.project_id, inp.user_id)

        # 4. Resolve effective company_id: prefer explicit override, then source doc's
        effective_company_id: UUID | None = inp.company_id if inp.company_id is not None else source.company_id

        # 5. company_id must always be non-None after phase 05 tightening
        if effective_company_id is None:
            raise MissingCompanyProfileError(inp.user_id)

        # 6. Validate attachment and snapshot from Company entity
        company = assert_user_company_access(self._access_repo, self._company_repo, inp.user_id, effective_company_id)
        if company is None:
            raise MissingCompanyProfileError(inp.user_id)

        issuer_snapshot = _snapshot_issuer_from_company(company)
        effective_prefix = _effective_prefix_from_company(company) or ""
        counter_key: UUID = effective_company_id
        default_payment_terms = company.default_payment_terms

        # 7. Atomically generate facture number
        today = datetime.now(timezone.utc).date()
        sequence = self._counter_repo.next_value(counter_key, BillingDocumentKind.FACTURE, today.year)
        document_number = next_document_number(
            prefix_override=effective_prefix,
            kind=BillingDocumentKind.FACTURE,
            year=today.year,
            sequence=sequence,
        )

        # 8. Resolve payment_terms
        payment_terms = inp.payment_terms if inp.payment_terms is not None else default_payment_terms

        # 9. Build new facture
        facture = _build_doc_from_inputs(
            user_id=inp.user_id,
            company_id=effective_company_id,
            kind=BillingDocumentKind.FACTURE,
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
            payment_due_date=inp.payment_due_date,
            payment_terms=payment_terms,
            source_devis_id=source.id,  # audit trail
        )

        saved = self._doc_repo.save(facture)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
