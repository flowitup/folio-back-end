"""Unit tests for UpdateBillingDocumentUseCase."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.update_billing_document_usecase import UpdateBillingDocumentUseCase
from app.application.billing.dtos import ItemInput, UpdateBillingDocumentInput
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import BillingDocumentNotFoundError, ForbiddenBillingDocumentError
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo):
    return UpdateBillingDocumentUseCase(doc_repo=doc_repo)


@pytest.fixture
def saved_doc(doc_repo, user_id):
    doc = make_doc(user_id=user_id)
    doc_repo.save(doc)
    return doc


class TestUpdateBillingDocumentHappyPath:
    def test_update_recipient_name(self, usecase, fake_session, user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, recipient_name="New Client")
        result = usecase.execute(inp, fake_session)
        assert result.recipient_name == "New Client"

    def test_update_notes(self, usecase, fake_session, user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, notes="Payment in 30 days")
        result = usecase.execute(inp, fake_session)
        assert result.notes == "Payment in 30 days"

    def test_update_items(self, usecase, fake_session, user_id, saved_doc):
        new_items = [
            ItemInput(
                description="New Service", quantity=Decimal("2"), unit_price=Decimal("200"), vat_rate=Decimal("10")
            )
        ]
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, items=new_items)
        result = usecase.execute(inp, fake_session)
        assert result.total_ht == Decimal("400")

    def test_immutable_fields_not_changed(self, usecase, fake_session, user_id, saved_doc):
        """kind, document_number, user_id, issuer_* must survive an update unchanged."""
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, notes="x")
        result = usecase.execute(inp, fake_session)
        assert result.kind == saved_doc.kind.value
        assert result.document_number == saved_doc.document_number
        assert result.issuer_legal_name == saved_doc.issuer_legal_name

    def test_updated_at_changes(self, usecase, fake_session, user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, notes="x")
        result = usecase.execute(inp, fake_session)
        assert result.updated_at >= saved_doc.updated_at

    def test_update_optional_contact_fields(self, usecase, fake_session, user_id, saved_doc):
        """Covers recipient_address, recipient_email, recipient_siret branches."""
        inp = UpdateBillingDocumentInput(
            id=saved_doc.id,
            user_id=user_id,
            recipient_address="42 rue Test",
            recipient_email="client@example.com",
            recipient_siret="98765432109876",
        )
        result = usecase.execute(inp, fake_session)
        assert result.recipient_address == "42 rue Test"
        assert result.recipient_email == "client@example.com"
        assert result.recipient_siret == "98765432109876"

    def test_update_terms_and_signature(self, usecase, fake_session, user_id, saved_doc):
        """Covers terms, signature_block_text branches."""
        inp = UpdateBillingDocumentInput(
            id=saved_doc.id,
            user_id=user_id,
            terms="Net 30",
            signature_block_text="Signed by CEO",
        )
        result = usecase.execute(inp, fake_session)
        assert result.terms == "Net 30"
        assert result.signature_block_text == "Signed by CEO"

    def test_update_devis_date_fields(self, usecase, fake_session, user_id, doc_repo):
        """Covers validity_until and issue_date branches on a devis (kind-compatible fields)."""
        devis_doc = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        doc_repo.save(devis_doc)
        inp = UpdateBillingDocumentInput(
            id=devis_doc.id,
            user_id=user_id,
            validity_until=date(2026, 3, 1),
            issue_date=date(2026, 2, 1),
        )
        result = usecase.execute(inp, fake_session)
        assert result.validity_until == date(2026, 3, 1)
        assert result.issue_date == date(2026, 2, 1)

    def test_update_facture_date_fields(self, usecase, fake_session, user_id, doc_repo):
        """Covers payment_due_date and payment_terms branches on a facture (kind-compatible fields)."""
        facture_doc = make_doc(user_id=user_id, kind=BillingDocumentKind.FACTURE, doc_number="FAC-2026-001")
        doc_repo.save(facture_doc)
        inp = UpdateBillingDocumentInput(
            id=facture_doc.id,
            user_id=user_id,
            payment_due_date=date(2026, 4, 1),
            payment_terms="30 days",
            issue_date=date(2026, 2, 1),
        )
        result = usecase.execute(inp, fake_session)
        assert result.payment_due_date == date(2026, 4, 1)
        assert result.payment_terms == "30 days"

    def test_update_project_id(self, usecase, fake_session, user_id, saved_doc):
        """Covers project_id branch."""
        pid = uuid4()
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, project_id=pid)
        result = usecase.execute(inp, fake_session)
        assert result.project_id == pid


class TestUpdateBillingDocumentErrors:
    def test_not_found_raises(self, usecase, fake_session, user_id):
        inp = UpdateBillingDocumentInput(id=uuid4(), user_id=user_id, notes="x")
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(inp, fake_session)

    def test_wrong_owner_raises(self, usecase, fake_session, other_user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=other_user_id, notes="x")
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(inp, fake_session)

    def test_devis_rejects_payment_due_date(self, usecase, fake_session, user_id, doc_repo):
        """M3: PUT a devis with payment_due_date → ValueError (kind-incompatible field)."""
        devis_doc = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        doc_repo.save(devis_doc)
        inp = UpdateBillingDocumentInput(id=devis_doc.id, user_id=user_id, payment_due_date=date(2026, 4, 1))
        with pytest.raises(ValueError, match="facture"):
            usecase.execute(inp, fake_session)

    def test_devis_rejects_payment_terms(self, usecase, fake_session, user_id, doc_repo):
        """M3: PUT a devis with payment_terms → ValueError (kind-incompatible field)."""
        devis_doc = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        doc_repo.save(devis_doc)
        inp = UpdateBillingDocumentInput(id=devis_doc.id, user_id=user_id, payment_terms="Net 30")
        with pytest.raises(ValueError, match="facture"):
            usecase.execute(inp, fake_session)

    def test_facture_rejects_validity_until(self, usecase, fake_session, user_id, doc_repo):
        """M3: PUT a facture with validity_until → ValueError (kind-incompatible field)."""
        facture_doc = make_doc(user_id=user_id, kind=BillingDocumentKind.FACTURE, doc_number="FAC-2026-999")
        doc_repo.save(facture_doc)
        inp = UpdateBillingDocumentInput(id=facture_doc.id, user_id=user_id, validity_until=date(2026, 6, 1))
        with pytest.raises(ValueError, match="devis"):
            usecase.execute(inp, fake_session)

    def test_empty_recipient_name_raises(self, usecase, fake_session, user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, recipient_name="  ")
        with pytest.raises(ValueError, match="Recipient name"):
            usecase.execute(inp, fake_session)

    def test_empty_items_list_raises(self, usecase, fake_session, user_id, saved_doc):
        inp = UpdateBillingDocumentInput(id=saved_doc.id, user_id=user_id, items=[])
        with pytest.raises(ValueError, match="At least one"):
            usecase.execute(inp, fake_session)
