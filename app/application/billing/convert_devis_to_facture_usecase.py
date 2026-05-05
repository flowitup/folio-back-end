"""ConvertDevisToFactureUseCase — convert an accepted devis into a new facture draft."""

from __future__ import annotations

from datetime import datetime, timezone

from app.application.billing._helpers import (
    _assert_owner,
    _build_doc_from_inputs,
    _snapshot_issuer,
)
from app.application.billing.dtos import BillingDocumentResponse, ConvertDevisToFactureInput
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    BillingNumberCounterRepositoryPort,
    CompanyProfileRepositoryPort,
    TransactionalSessionPort,
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
      - find_by_source_devis_id check performed inside the same transaction to
        prevent two concurrent converts on the same devis.
      - The DB unique constraint on (source_devis_id) is the final backstop.

    Pre-conditions:
      - source_devis must exist, be owned by user, kind=DEVIS, status=ACCEPTED.
      - No existing facture linked to source_devis_id (raises DevisAlreadyConvertedError).
      - User must have a CompanyProfile.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        counter_repo: BillingNumberCounterRepositoryPort,
        profile_repo: CompanyProfileRepositoryPort,
    ) -> None:
        self._doc_repo = doc_repo
        self._counter_repo = counter_repo
        self._profile_repo = profile_repo

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

        # 4. Require company profile
        profile = self._profile_repo.find_by_user_id(inp.user_id)
        if profile is None:
            raise MissingCompanyProfileError(inp.user_id)
        issuer_snapshot = _snapshot_issuer(profile)

        # 5. Atomically generate facture number
        today = datetime.now(timezone.utc).date()
        sequence = self._counter_repo.next_value(inp.user_id, BillingDocumentKind.FACTURE, today.year)
        document_number = next_document_number(
            prefix_override=profile.effective_prefix,
            kind=BillingDocumentKind.FACTURE,
            year=today.year,
            sequence=sequence,
        )

        # 6. Resolve payment_terms
        payment_terms = inp.payment_terms if inp.payment_terms is not None else profile.default_payment_terms

        # 7. Build new facture
        facture = _build_doc_from_inputs(
            user_id=inp.user_id,
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
            payment_due_date=inp.payment_due_date,  # None → default applied in helper
            payment_terms=payment_terms,
            source_devis_id=source.id,  # audit trail
        )

        saved = self._doc_repo.save(facture)
        db_session.commit()
        return BillingDocumentResponse.from_entity(saved)
