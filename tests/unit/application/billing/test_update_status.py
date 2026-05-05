"""Unit tests for UpdateBillingDocumentStatusUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.update_billing_document_status_usecase import UpdateBillingDocumentStatusUseCase
from app.application.billing.dtos import UpdateStatusInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import (
    BillingDocumentNotFoundError,
    ForbiddenBillingDocumentError,
    InvalidStatusTransitionError,
)
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo):
    return UpdateBillingDocumentStatusUseCase(doc_repo=doc_repo)


class TestUpdateStatusHappyPath:
    def test_draft_to_sent(self, usecase, doc_repo, fake_session, user_id):
        doc = make_doc(user_id=user_id, status=BillingDocumentStatus.DRAFT)
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.SENT)
        result = usecase.execute(inp, fake_session)
        assert result.status == "sent"

    def test_sent_to_accepted_devis(self, usecase, doc_repo, fake_session, user_id):
        doc = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS, status=BillingDocumentStatus.SENT)
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.ACCEPTED)
        result = usecase.execute(inp, fake_session)
        assert result.status == "accepted"

    def test_sent_to_paid_facture(self, usecase, doc_repo, fake_session, user_id):
        doc = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            status=BillingDocumentStatus.SENT,
            doc_number="FAC-2026-001",
        )
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.PAID)
        result = usecase.execute(inp, fake_session)
        assert result.status == "paid"

    def test_updated_at_changes(self, usecase, doc_repo, fake_session, user_id):
        doc = make_doc(user_id=user_id, status=BillingDocumentStatus.DRAFT)
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.SENT)
        result = usecase.execute(inp, fake_session)
        assert result.updated_at >= doc.updated_at


class TestUpdateStatusErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id):
        inp = UpdateStatusInput(id=uuid4(), user_id=user_id, new_status=BillingDocumentStatus.SENT)
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_wrong_owner_raises(self, usecase, doc_repo, fake_session, other_user_id, user_id):
        doc = make_doc(user_id=user_id, status=BillingDocumentStatus.DRAFT)
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=other_user_id, new_status=BillingDocumentStatus.SENT)
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(inp, fake_session)

    def test_invalid_transition_raises(self, usecase, doc_repo, fake_session, user_id):
        doc = make_doc(user_id=user_id, status=BillingDocumentStatus.DRAFT)
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.PAID)
        with pytest.raises(InvalidStatusTransitionError):
            usecase.execute(inp, fake_session)
