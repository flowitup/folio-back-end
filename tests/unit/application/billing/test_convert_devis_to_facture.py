"""Unit tests for ConvertDevisToFactureUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.convert_devis_to_facture_usecase import ConvertDevisToFactureUseCase
from app.application.billing.dtos import ConvertDevisToFactureInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import (
    BillingDocumentNotFoundError,
    DevisAlreadyConvertedError,
    ForbiddenBillingDocumentError,
    MissingCompanyProfileError,
)
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo, counter_repo, company_repo, access_repo):
    return ConvertDevisToFactureUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        project_repo=None,
        company_repo=company_repo,
        access_repo=access_repo,
    )


@pytest.fixture
def accepted_devis(doc_repo, user_id, company_id, seeded_company):
    """Accepted devis with company_id set so convert can resolve issuer."""
    doc = make_doc(
        user_id=user_id,
        kind=BillingDocumentKind.DEVIS,
        status=BillingDocumentStatus.ACCEPTED,
        company_id=company_id,
    )
    doc_repo.save(doc)
    return doc


class TestConvertDevisToFactureHappyPath:
    def test_converts_accepted_devis_to_facture(self, usecase, fake_session, user_id, seeded_company, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)

        assert result.kind == "facture"
        assert result.status == "draft"
        assert result.source_devis_id == accepted_devis.id
        assert result.recipient_name == accepted_devis.recipient_name
        assert result.items[0].description == accepted_devis.items[0].description

    def test_facture_has_new_document_number(self, usecase, fake_session, user_id, seeded_company, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert "FAC" in result.document_number

    def test_source_devis_id_set_on_facture(self, usecase, fake_session, user_id, seeded_company, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.source_devis_id == accepted_devis.id

    def test_issuer_snapshot_from_company(self, usecase, fake_session, user_id, seeded_company, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.issuer_legal_name == seeded_company.legal_name


class TestConvertDevisToFactureErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id, seeded_company):
        inp = ConvertDevisToFactureInput(source_devis_id=uuid4(), user_id=user_id)
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_wrong_owner_raises(self, usecase, fake_session, user_id, other_user_id, seeded_company, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=other_user_id)
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(inp, fake_session)

    def test_convert_facture_source_raises(self, usecase, fake_session, user_id, company_id, seeded_company, doc_repo):
        """Spec #8: facture as source → ValueError (mapped to 400 by route)."""
        facture = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            status=BillingDocumentStatus.ACCEPTED,
            doc_number="FAC-2026-001",
            company_id=company_id,
        )
        doc_repo.save(facture)
        inp = ConvertDevisToFactureInput(source_devis_id=facture.id, user_id=user_id)
        with pytest.raises(ValueError, match="not a devis"):
            usecase.execute(inp, fake_session)

    def test_convert_devis_not_accepted_raises(self, usecase, fake_session, user_id, company_id, seeded_company,
                                               doc_repo):
        """Spec #9: devis with status=draft → ValueError."""
        draft_devis = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.DRAFT,
            company_id=company_id,
        )
        doc_repo.save(draft_devis)
        inp = ConvertDevisToFactureInput(source_devis_id=draft_devis.id, user_id=user_id)
        with pytest.raises(ValueError, match="accepted"):
            usecase.execute(inp, fake_session)

    def test_convert_devis_already_converted_raises(self, usecase, fake_session, user_id, seeded_company,
                                                     doc_repo, accepted_devis):
        """Spec #7: second convert call on the same accepted devis fails."""
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        usecase.execute(inp, fake_session)  # first succeeds

        with pytest.raises(DevisAlreadyConvertedError) as exc_info:
            usecase.execute(inp, fake_session)
        assert exc_info.value.devis_id == accepted_devis.id

    def test_missing_company_id_in_source_raises(self, usecase, fake_session, user_id, doc_repo):
        """Source doc has no company_id → MissingCompanyProfileError."""
        devis = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.ACCEPTED,
            company_id=None,
        )
        doc_repo.save(devis)
        inp = ConvertDevisToFactureInput(source_devis_id=devis.id, user_id=user_id)
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(inp, fake_session)
