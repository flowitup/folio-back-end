"""Unit tests for BillingDocument.with_updates() immutability semantics."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.value_objects import BillingDocumentItem


def _make_doc(**overrides):
    defaults = dict(
        id=uuid4(),
        user_id=uuid4(),
        kind=BillingDocumentKind.DEVIS,
        document_number="DEV-2026-001",
        status=BillingDocumentStatus.DRAFT,
        issue_date=date(2026, 1, 1),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        recipient_name="Acme Corp",
        issuer_legal_name="My Company SAS",
        issuer_address="1 rue de la Paix, 75001 Paris",
        items=(
            BillingDocumentItem(
                description="Service",
                quantity=Decimal("1"),
                unit_price=Decimal("1000"),
                vat_rate=Decimal("20"),
            ),
        ),
    )
    defaults.update(overrides)
    return BillingDocument(**defaults)


class TestWithUpdates:
    def test_with_updates_returns_new_instance(self):
        doc = _make_doc()
        updated = doc.with_updates(recipient_name="New Client")
        assert updated is not doc

    def test_with_updates_modifies_target_field(self):
        doc = _make_doc()
        updated = doc.with_updates(recipient_name="Changed")
        assert updated.recipient_name == "Changed"

    def test_with_updates_preserves_unchanged_fields(self):
        doc = _make_doc()
        updated = doc.with_updates(notes="Some notes")
        assert updated.id == doc.id
        assert updated.user_id == doc.user_id
        assert updated.kind == doc.kind
        assert updated.document_number == doc.document_number
        assert updated.issuer_legal_name == doc.issuer_legal_name
        assert updated.issuer_address == doc.issuer_address

    def test_with_updates_status_transition(self):
        doc = _make_doc(status=BillingDocumentStatus.DRAFT)
        updated = doc.with_updates(status=BillingDocumentStatus.SENT)
        assert updated.status == BillingDocumentStatus.SENT
        assert doc.status == BillingDocumentStatus.DRAFT  # original unchanged

    def test_original_document_is_immutable(self):
        """BillingDocument is frozen — direct attribute mutation raises."""
        doc = _make_doc()
        with pytest.raises((AttributeError, TypeError)):
            doc.recipient_name = "Mutated"  # type: ignore[misc]

    def test_identity_based_equality(self):
        """Two docs with the same id are equal even if other fields differ."""
        doc_id = uuid4()
        doc_a = _make_doc(id=doc_id, recipient_name="A")
        doc_b = _make_doc(id=doc_id, recipient_name="B")
        assert doc_a == doc_b

    def test_different_ids_not_equal(self):
        doc_a = _make_doc(id=uuid4())
        doc_b = _make_doc(id=uuid4())
        assert doc_a != doc_b

    def test_hash_by_id(self):
        doc_id = uuid4()
        doc = _make_doc(id=doc_id)
        assert hash(doc) == hash(doc_id)

    def test_computed_totals_update_on_item_change(self):
        doc = _make_doc(
            items=(
                BillingDocumentItem(
                    description="Widget",
                    quantity=Decimal("2"),
                    unit_price=Decimal("100"),
                    vat_rate=Decimal("20"),
                ),
            )
        )
        assert doc.total_ht == Decimal("200")
        assert doc.total_tva == Decimal("40")
        assert doc.total_ttc == Decimal("240")

        new_items = (
            BillingDocumentItem(
                description="Widget",
                quantity=Decimal("5"),
                unit_price=Decimal("100"),
                vat_rate=Decimal("20"),
            ),
        )
        updated = doc.with_updates(items=new_items)
        assert updated.total_ht == Decimal("500")
        assert updated.total_ttc == Decimal("600")
