"""Unit tests for UpdateBillingDocumentStatusUseCase."""

from decimal import ROUND_HALF_UP, Decimal
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
from app.domain.value_objects.invoice_item import InvoiceItem
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
    """Compute TTC total from structured items (quantity × unit_price × (1 + vat_rate/100))."""
    total = Decimal("0")
    for it in amount_items:
        qty = Decimal(it["quantity"])
        price = Decimal(it["unit_price"])
        vat = Decimal(it.get("vat_rate", "0"))
        total += qty * price * (1 + vat / Decimal("100"))
    return total


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

    def test_paid_facture_copies_items_with_vat_rate(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        """Each billing line is copied verbatim with its vat_rate; no synthetic TVA lines."""
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
            {"description": "Acompte", "quantity": "1", "unit_price": "100", "vat_rate": "20"},
        ]
        # No synthetic TVA lines should be present
        assert not any(it["description"].startswith("TVA") for it in call["amount_items"])

    def test_expense_total_matches_facture_ttc(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        # Real-world regression: HT 66287.23 @ 20% → TTC 79544.676 ≈ 79544.68
        doc = self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Acompte 3%", qty="1", price="66287.23", vat="20"),),
        )
        total = _expense_total(funds_release.created[0]["amount_items"])
        # The expense keeps the facture's lines (66287.23 × 1.20 = 79544.676); the
        # facture rounds its TVA to the cent, and both show 79544.68.
        assert total == Decimal("79544.676")
        assert total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == doc.total_ttc == Decimal("79544.68")

    @pytest.mark.parametrize(
        "items, expected_ttc",
        [
            # HT 49.975 → 49.98 and 508.2025 → 508.20 on the facture; the expense maths
            # (TTC rounded once) gave 59.97 for the first line, so the release was 618.99.
            ((("Peinture m2", "2.5", "19.99", "20"), ("Enduit", "12.35", "41.15", "10")), Decimal("619.00")),
            # 0.125 → 0.13 HT + 0.03 TVA per line on the facture, but 0.15 per expense line.
            ((("A", "1", "0.125", "20"), ("B", "1", "0.125", "20")), Decimal("0.32")),
        ],
    )
    def test_release_total_equals_facture_ttc_when_line_ht_needs_rounding(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id, items, expected_ttc
    ):
        doc = self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=tuple(make_item(desc=d, qty=q, price=p, vat=v) for d, q, p, v in items),
        )
        assert doc.total_ttc == expected_ttc
        released = funds_release.created[0]["amount_items"]
        expense_lines = [
            InvoiceItem(
                description=it["description"],
                quantity=Decimal(it["quantity"]),
                unit_price=Decimal(it["unit_price"]),
                vat_rate=Decimal(it["vat_rate"]),
            )
            for it in released
        ]
        assert [line.total for line in expense_lines] == [it.total_ttc for it in doc.items]
        assert sum((line.total for line in expense_lines), Decimal("0")) == expected_ttc
        # A line that needs rounding is released as one unit at its rounded HT.
        assert released[0] == {
            "description": items[0][0],
            "quantity": "1",
            "unit_price": str(doc.items[0].total_ht),
            "vat_rate": items[0][3],
        }

    def test_release_keeps_quantity_and_unit_price_when_line_ht_is_at_the_cent(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Carrelage", qty="2.5", price="19.98", vat="20"),),
        )
        assert funds_release.created[0]["amount_items"] == [
            {"description": "Carrelage", "quantity": "2.5", "unit_price": "19.98", "vat_rate": "20"},
        ]

    def test_multiple_items_with_different_vat_rates(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        """Multiple items each carry their own vat_rate — no merging or TVA lines."""
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
        items = funds_release.created[0]["amount_items"]
        assert items == [
            {"description": "A", "quantity": "1", "unit_price": "100", "vat_rate": "10"},
            {"description": "B", "quantity": "1", "unit_price": "100", "vat_rate": "20"},
            {"description": "C", "quantity": "1", "unit_price": "100", "vat_rate": "20"},
        ]
        # No TVA lines
        assert not any(it["description"].startswith("TVA") for it in items)

    def test_zero_vat_rate_item_copied_with_zero_rate(
        self, usecase_with_funds, funds_release, doc_repo, fake_session, user_id
    ):
        """Items with 0% VAT are copied as-is; no TVA line generated."""
        self._paid_facture(
            doc_repo,
            fake_session,
            usecase_with_funds,
            user_id,
            items=(make_item(desc="Exonéré", qty="1", price="100", vat="0"),),
        )
        items = funds_release.created[0]["amount_items"]
        assert items == [{"description": "Exonéré", "quantity": "1", "unit_price": "100", "vat_rate": "0"}]

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
