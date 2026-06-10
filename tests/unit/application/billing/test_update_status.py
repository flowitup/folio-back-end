"""Unit tests for UpdateBillingDocumentStatusUseCase."""

from decimal import Decimal
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
from tests.unit.application.billing.conftest import make_doc, make_item


class FakeFundsRelease:
    """Recorder implementing FundsReleasePort for bridge assertions."""

    def __init__(self):
        self.created: list[dict] = []
        self.deleted: list = []

    def create_funds_release(self, project_id, source_doc_id, amount_items, recipient_name, issue_date, created_by):
        self.created.append(
            {
                "project_id": project_id,
                "source_doc_id": source_doc_id,
                "amount_items": amount_items,
                "recipient_name": recipient_name,
                "issue_date": issue_date,
                "created_by": created_by,
            }
        )

    def delete_funds_release(self, source_doc_id):
        self.deleted.append(source_doc_id)


@pytest.fixture
def usecase(doc_repo):
    return UpdateBillingDocumentStatusUseCase(doc_repo=doc_repo)


@pytest.fixture
def funds_release():
    return FakeFundsRelease()


@pytest.fixture
def usecase_with_funds(doc_repo, funds_release):
    return UpdateBillingDocumentStatusUseCase(doc_repo=doc_repo, funds_release=funds_release)


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


def _expense_total(amount_items: list) -> Decimal:
    return sum(
        (Decimal(it["quantity"]) * Decimal(it["unit_price"]) for it in amount_items),
        Decimal("0"),
    )


class TestFundsReleaseBridge:
    def _paid_facture(self, doc_repo, fake_session, usecase, user_id, **overrides):
        doc = make_doc(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            status=BillingDocumentStatus.SENT,
            doc_number="FAC-2026-001",
            project_id=overrides.pop("project_id", uuid4()),
            **overrides,
        )
        doc_repo.save(doc)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.PAID)
        usecase.execute(inp, fake_session)
        return doc

    def test_paid_facture_creates_release_with_tva_line(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        doc = self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Acompte", qty="1", price="100", vat="20"),),
        )
        assert len(funds_release.created) == 1
        call = funds_release.created[0]
        assert call["source_doc_id"] == doc.id
        assert call["amount_items"] == [
            {"description": "Acompte", "quantity": "1", "unit_price": "100"},
            {"description": "TVA 20%", "quantity": "1", "unit_price": "20.00"},
        ]

    def test_expense_total_matches_facture_ttc(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        # Real-world regression: HT 66287.23 @ 20% → TTC 79544.68 (TVA 13257.446 → 13257.45)
        doc = self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Acompte 3%", qty="1", price="66287.23", vat="20"),),
        )
        total = _expense_total(funds_release.created[0]["amount_items"])
        assert total == Decimal("79544.68")
        assert total == doc.total_ttc.quantize(Decimal("0.01"))

    def test_tva_lines_grouped_per_rate_sorted_desc(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(
                make_item(desc="A", qty="1", price="100", vat="10"),
                make_item(desc="B", qty="1", price="100", vat="20"),
                make_item(desc="C", qty="1", price="100", vat="20"),
            ),
        )
        tva_lines = [it for it in funds_release.created[0]["amount_items"] if it["description"].startswith("TVA")]
        assert tva_lines == [
            {"description": "TVA 20%", "quantity": "1", "unit_price": "40.00"},
            {"description": "TVA 10%", "quantity": "1", "unit_price": "10.00"},
        ]

    def test_zero_vat_rate_produces_no_tva_line(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Exonéré", qty="1", price="100", vat="0"),),
        )
        descriptions = [it["description"] for it in funds_release.created[0]["amount_items"]]
        assert descriptions == ["Exonéré"]

    def test_no_project_id_skips_release(self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id):
        self._paid_facture(doc_repo, fake_session, usecase_with_funds, user_id, project_id=None)
        assert funds_release.created == []

    def test_paid_to_cancelled_deletes_release(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        doc = self._paid_facture(doc_repo, fake_session, usecase_with_funds, user_id)
        inp = UpdateStatusInput(id=doc.id, user_id=user_id, new_status=BillingDocumentStatus.CANCELLED)
        usecase_with_funds.execute(inp, fake_session)
        assert funds_release.deleted == [doc.id]
