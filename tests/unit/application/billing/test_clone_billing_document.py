"""Unit tests for CloneBillingDocumentUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.clone_billing_document_usecase import CloneBillingDocumentUseCase
from app.application.billing.dtos import CloneBillingDocumentInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import BillingDocumentNotFoundError, MissingCompanyProfileError
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo, counter_repo, profile_repo):
    return CloneBillingDocumentUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        profile_repo=profile_repo,
    )


@pytest.fixture
def source_doc(doc_repo, user_id):
    # Use a non-counter-colliding number so the clone's counter-generated
    # number (DEV-2026-001) is guaranteed to differ from the source.
    doc = make_doc(user_id=user_id, status=BillingDocumentStatus.SENT, doc_number="DEV-2026-099")
    doc_repo.save(doc)
    return doc


class TestCloneBillingDocumentHappyPath:
    def test_clone_creates_new_doc(self, usecase, doc_repo, fake_session, user_id, profile, source_doc):
        inp = CloneBillingDocumentInput(source_id=source_doc.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)

        assert result.id != source_doc.id
        assert result.status == "draft"
        assert result.kind == source_doc.kind.value
        assert result.recipient_name == source_doc.recipient_name

    def test_clone_resets_source_devis_id_to_none(self, usecase, doc_repo, fake_session, user_id, profile):
        """Spec: source_devis_id is None on a clone (only set on convert)."""
        # Create a facture that was converted from a devis
        converted = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            doc_number="FAC-2026-001",
            source_devis_id=uuid4(),
        )
        doc_repo.save(converted)
        inp = CloneBillingDocumentInput(source_id=converted.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.source_devis_id is None

    def test_clone_gets_new_document_number(self, usecase, fake_session, user_id, profile, source_doc):
        inp = CloneBillingDocumentInput(source_id=source_doc.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.document_number != source_doc.document_number

    def test_clone_with_override_kind(self, usecase, fake_session, user_id, profile, source_doc):
        inp = CloneBillingDocumentInput(
            source_id=source_doc.id,
            user_id=user_id,
            override_kind=BillingDocumentKind.FACTURE,
        )
        result = usecase.execute(inp, fake_session)
        assert result.kind == "facture"


class TestCloneBillingDocumentErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id, profile):
        inp = CloneBillingDocumentInput(source_id=uuid4(), user_id=user_id)
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_missing_profile_raises(self, usecase, fake_session, user_id, source_doc):
        # profile_repo has no profile for user_id
        inp = CloneBillingDocumentInput(source_id=source_doc.id, user_id=user_id)
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(inp, fake_session)
