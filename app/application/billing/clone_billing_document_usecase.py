"""CloneBillingDocumentUseCase — clone an existing billing document into a new draft."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import (
    _assert_owner,
    _build_doc_from_inputs,
    _snapshot_issuer,
)
from app.application.billing.dtos import BillingDocumentResponse, CloneBillingDocumentInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    CompanyProfileRepositoryPort,
    ProjectReadPort,
    TransactionalSessionPort,
    assert_project_read_access,
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
      - Issuer snapshot is taken from CURRENT company_profile, not the source doc.
      - source_devis_id is NOT copied (this is a plain clone, not a convert).
      - kind: use override_kind if supplied, else same kind as source.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        profile_repo: CompanyProfileRepositoryPort,
        project_repo: ProjectReadPort = None,  # type: ignore[assignment]
    ) -> None:
        self._doc_repo = doc_repo
        self._counter_repo = counter_repo
        self._profile_repo = profile_repo
        self._project_repo = project_repo

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

        # 2. Require company profile — snapshot CURRENT issuer
        profile = self._profile_repo.find_by_user_id(inp.user_id)
        if profile is None:
            raise MissingCompanyProfileError(inp.user_id)
        issuer_snapshot = _snapshot_issuer(profile)

        # 3. Determine target kind
        target_kind = inp.override_kind if inp.override_kind is not None else source.kind

        # H1: Verify project:read access if source doc has a project_id
        assert_project_read_access(self._project_repo, source.project_id, inp.user_id)

        # 4. Atomically generate new document number
        today = datetime.now(timezone.utc).date()
        sequence = self._counter_repo.next_value(inp.user_id, target_kind, today.year)
        document_number = next_document_number(
            prefix_override=profile.effective_prefix,
            kind=target_kind,
            year=today.year,
            sequence=sequence,
        )

        # 5. Build new doc — dates reset, issuer from current profile.
        # H2: When changing kind, zero out the fields that are incompatible with the
        # target kind to avoid violating the ck_billing_doc_payment_fields_facture_only
        # DB CHECK constraint (kind='devis' must have payment_terms=NULL, payment_due_date=NULL;
        # kind='facture' must have validity_until=NULL — enforced in _build_doc_from_inputs).
        source_payment_terms = source.payment_terms
        if target_kind == BillingDocumentKind.DEVIS and source.kind != BillingDocumentKind.DEVIS:
            # facture → devis: drop payment fields
            source_payment_terms = None

        doc = _build_doc_from_inputs(
            user_id=inp.user_id,
            kind=target_kind,
            document_number=document_number,
            issuer_snapshot=issuer_snapshot,
            recipient_name=source.recipient_name,
            items=source.items,  # already tuple[BillingDocumentItem, ...]
            issue_date=today,
            project_id=source.project_id,
            recipient_address=source.recipient_address,
            recipient_email=source.recipient_email,
            recipient_siret=source.recipient_siret,
            notes=source.notes,
            terms=source.terms,
            signature_block_text=source.signature_block_text,
            # validity_until / payment_due_date left None → defaults applied in _build_doc_from_inputs.
            # For devis→facture clone, validity_until=None is already correct (helper defaults it to
            # None for facture). For facture→devis, passing None clears the devis validity_until
            # so the helper's +30-day default is applied instead.
            payment_terms=source_payment_terms,
            source_devis_id=None,  # plain clone — no audit trail link
        )

        saved = self._doc_repo.save(doc)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
