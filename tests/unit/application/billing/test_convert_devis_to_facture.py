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
def usecase(doc_repo, counter_repo, profile_repo):
    return ConvertDevisToFactureUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        profile_repo=profile_repo,
    )


@pytest.fixture
def accepted_devis(doc_repo, user_id):
    doc = make_doc(
        user_id=user_id,
        kind=BillingDocumentKind.DEVIS,
        status=BillingDocumentStatus.ACCEPTED,
    )
    doc_repo.save(doc)
    return doc


class TestConvertDevisToFactureHappyPath:
    def test_converts_accepted_devis_to_facture(self, usecase, fake_session, user_id, profile, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)

        assert result.kind == "facture"
        assert result.status == "draft"
        assert result.source_devis_id == accepted_devis.id
        assert result.recipient_name == accepted_devis.recipient_name
        assert result.items[0].description == accepted_devis.items[0].description

    def test_facture_has_new_document_number(self, usecase, fake_session, user_id, profile, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert "FAC" in result.document_number

    def test_source_devis_id_set_on_facture(self, usecase, fake_session, user_id, profile, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.source_devis_id == accepted_devis.id

    def test_issuer_snapshot_from_profile(self, usecase, fake_session, user_id, profile, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.issuer_legal_name == profile.legal_name


class TestConvertDevisToFactureErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id, profile):
        inp = ConvertDevisToFactureInput(source_devis_id=uuid4(), user_id=user_id)
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_wrong_owner_raises(self, usecase, fake_session, user_id, other_user_id, profile, accepted_devis):
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=other_user_id)
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(inp, fake_session)

    def test_convert_facture_source_returns_422(self, usecase, fake_session, user_id, profile, doc_repo):
        """Spec #8: facture as source → ValueError (mapped to 422 by route)."""
        facture = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            status=BillingDocumentStatus.ACCEPTED,
            doc_number="FAC-2026-001",
        )
        doc_repo.save(facture)
        inp = ConvertDevisToFactureInput(source_devis_id=facture.id, user_id=user_id)
        with pytest.raises(ValueError, match="not a devis"):
            usecase.execute(inp, fake_session)

    def test_convert_devis_not_accepted_returns_422(self, usecase, fake_session, user_id, profile, doc_repo):
        """Spec #9: devis with status=draft → ValueError."""
        draft_devis = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.DRAFT,
        )
        doc_repo.save(draft_devis)
        inp = ConvertDevisToFactureInput(source_devis_id=draft_devis.id, user_id=user_id)
        with pytest.raises(ValueError, match="accepted"):
            usecase.execute(inp, fake_session)

    def test_convert_devis_already_converted_returns_409(
        self, usecase, fake_session, user_id, profile, doc_repo, accepted_devis
    ):
        """Spec #7: second convert call on the same accepted devis fails."""
        # First conversion succeeds
        inp = ConvertDevisToFactureInput(source_devis_id=accepted_devis.id, user_id=user_id)
        usecase.execute(inp, fake_session)

        # Second conversion must raise DevisAlreadyConvertedError
        with pytest.raises(DevisAlreadyConvertedError) as exc_info:
            usecase.execute(inp, fake_session)
        assert exc_info.value.devis_id == accepted_devis.id

    def test_missing_company_profile_raises(self, usecase, fake_session, user_id, doc_repo):
        # No profile saved — profile_repo empty
        devis = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.ACCEPTED,
        )
        doc_repo.save(devis)
        inp = ConvertDevisToFactureInput(source_devis_id=devis.id, user_id=user_id)
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(inp, fake_session)
