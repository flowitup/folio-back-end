"""Unit tests for CloneBillingDocumentUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.clone_billing_document_usecase import CloneBillingDocumentUseCase
from app.application.billing.dtos import CloneBillingDocumentInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import BillingDocumentNotFoundError, MissingCompanyProfileError
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo, counter_repo, profile_repo, company_repo, access_repo):
    return CloneBillingDocumentUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        profile_repo=profile_repo,
        company_repo=company_repo,
        access_repo=access_repo,
    )


@pytest.fixture
def source_doc(doc_repo, user_id, company_id, seeded_company):
    """Source doc with company_id set so clone can resolve issuer."""
    doc = make_doc(user_id=user_id, status=BillingDocumentStatus.SENT, doc_number="DEV-2026-099",
                   company_id=company_id)
    doc_repo.save(doc)
    return doc


class TestCloneBillingDocumentHappyPath:
    def test_clone_creates_new_doc(self, usecase, doc_repo, fake_session, user_id, seeded_company, source_doc):
        inp = CloneBillingDocumentInput(source_id=source_doc.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)

        assert result.id != source_doc.id
        assert result.status == "draft"
        assert result.kind == source_doc.kind.value
        assert result.recipient_name == source_doc.recipient_name

    def test_clone_resets_source_devis_id_to_none(self, usecase, doc_repo, fake_session, user_id,
                                                   company_id, seeded_company):
        """Spec: source_devis_id is None on a clone (only set on convert)."""
        converted = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            doc_number="FAC-2026-001",
            source_devis_id=uuid4(),
            company_id=company_id,
        )
        doc_repo.save(converted)
        inp = CloneBillingDocumentInput(source_id=converted.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.source_devis_id is None

    def test_clone_gets_new_document_number(self, usecase, fake_session, user_id, seeded_company, source_doc):
        inp = CloneBillingDocumentInput(source_id=source_doc.id, user_id=user_id)
        result = usecase.execute(inp, fake_session)
        assert result.document_number != source_doc.document_number

    def test_clone_with_override_kind(self, usecase, fake_session, user_id, seeded_company, source_doc):
        inp = CloneBillingDocumentInput(
            source_id=source_doc.id,
            user_id=user_id,
            override_kind=BillingDocumentKind.FACTURE,
        )
        result = usecase.execute(inp, fake_session)
        assert result.kind == "facture"


class TestCloneBillingDocumentErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id, seeded_company):
        inp = CloneBillingDocumentInput(source_id=uuid4(), user_id=user_id)
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_missing_company_id_in_source_raises(self, usecase, doc_repo, fake_session, user_id):
        """Source doc has company_id=None + inp has no company_id → MissingCompanyProfileError."""
        doc = make_doc(user_id=user_id, status=BillingDocumentStatus.SENT, doc_number="DEV-2026-099",
                       company_id=None)
        doc_repo.save(doc)
        inp = CloneBillingDocumentInput(source_id=doc.id, user_id=user_id)
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(inp, fake_session)
