"""Integration tests for SqlAlchemyBillingDocumentRepository against SQLite.

These tests run against the in-memory SQLite DB created by the session fixture
from tests/conftest.py. They verify the full serialize→persist→deserialize round-trip.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.value_objects import BillingDocumentItem
from app.infrastructure.database.repositories.sqlalchemy_billing_document_repository import (
    SqlAlchemyBillingDocumentRepository,
)
from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user(session) -> UUID:
    """Insert a minimal UserModel row and return its UUID."""
    user = UserModel(
        id=uuid4(),
        email=f"billing-{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return UUID(str(user.id))


def _make_doc(user_id: UUID, kind=BillingDocumentKind.DEVIS, **overrides) -> BillingDocument:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        user_id=user_id,
        kind=kind,
        document_number=f"DEV-2026-{uuid4().hex[:6]}",
        status=BillingDocumentStatus.DRAFT,
        issue_date=date(2026, 1, 15),
        created_at=now,
        updated_at=now,
        recipient_name="Test Client",
        issuer_legal_name="My Company SAS",
        issuer_address="1 rue de la Paix, Paris",
        items=(
            BillingDocumentItem(
                description="Service",
                quantity=Decimal("1"),
                unit_price=Decimal("500"),
                vat_rate=Decimal("20"),
            ),
        ),
    )
    defaults.update(overrides)
    return BillingDocument(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBillingDocumentRepositoryCRUD:
    def test_save_and_find_by_id(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        doc = _make_doc(user_id)
        saved = repo.save(doc)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert found is not None
        assert found.id == saved.id
        assert found.recipient_name == "Test Client"

    def test_find_by_id_missing_returns_none(self, session):
        repo = SqlAlchemyBillingDocumentRepository(session)
        assert repo.find_by_id(uuid4()) is None

    def test_find_by_id_for_update(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        doc = _make_doc(user_id)
        saved = repo.save(doc)
        found = repo.find_by_id_for_update(UUID(str(saved.id)))
        assert found is not None
        assert found.id == saved.id

    def test_update_via_save(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        doc = _make_doc(user_id)
        saved = repo.save(doc)
        updated = saved.with_updates(recipient_name="Updated Client")
        repo.save(updated)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert found.recipient_name == "Updated Client"

    def test_delete(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        doc = _make_doc(user_id)
        saved = repo.save(doc)
        repo.delete(UUID(str(saved.id)))
        session.flush()
        assert repo.find_by_id(UUID(str(saved.id))) is None

    def test_delete_nonexistent_is_noop(self, session):
        repo = SqlAlchemyBillingDocumentRepository(session)
        repo.delete(uuid4())  # should not raise

    def test_list_for_user_filters_by_kind(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        devis = _make_doc(user_id, kind=BillingDocumentKind.DEVIS)
        facture = _make_doc(
            user_id,
            kind=BillingDocumentKind.FACTURE,
            document_number="FAC-2026-001",
            validity_until=None,
            payment_due_date=date(2026, 2, 15),
        )
        repo.save(devis)
        repo.save(facture)

        results, total = repo.list_for_user(user_id, BillingDocumentKind.DEVIS)
        assert total == 1
        assert results[0].kind == BillingDocumentKind.DEVIS

    def test_list_for_user_filters_by_status(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        draft = _make_doc(user_id, document_number="DEV-2026-D01")
        sent = _make_doc(user_id, status=BillingDocumentStatus.SENT, document_number="DEV-2026-S01")
        repo.save(draft)
        repo.save(sent)

        results, total = repo.list_for_user(user_id, BillingDocumentKind.DEVIS, status=BillingDocumentStatus.SENT)
        assert total == 1
        assert results[0].status == BillingDocumentStatus.SENT

    def test_list_pagination(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        for i in range(5):
            repo.save(_make_doc(user_id, document_number=f"DEV-2026-{i+1:03d}"))

        page1, total = repo.list_for_user(user_id, BillingDocumentKind.DEVIS, limit=3, offset=0)
        page2, _ = repo.list_for_user(user_id, BillingDocumentKind.DEVIS, limit=3, offset=3)
        assert total == 5
        assert len(page1) == 3
        assert len(page2) == 2

    def test_find_by_source_devis_id(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        devis = _make_doc(user_id, document_number="DEV-2026-SRC")
        repo.save(devis)

        facture = _make_doc(
            user_id,
            kind=BillingDocumentKind.FACTURE,
            document_number="FAC-2026-001",
            source_devis_id=UUID(str(devis.id)),
            validity_until=None,
            payment_due_date=date(2026, 2, 15),
        )
        repo.save(facture)

        found = repo.find_by_source_devis_id(UUID(str(devis.id)))
        assert found is not None
        assert found.kind == BillingDocumentKind.FACTURE

    def test_decimal_precision_survives_round_trip(self, session):
        """Spec: Decimal precision preserved through serialise/deserialise round-trip."""
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)
        item = BillingDocumentItem(
            description="Precise",
            quantity=Decimal("1.5"),
            unit_price=Decimal("99.99"),
            vat_rate=Decimal("5.5"),
        )
        doc = _make_doc(user_id, items=(item,))
        saved = repo.save(doc)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert found.items[0].quantity == Decimal("1.5")
        assert found.items[0].unit_price == Decimal("99.99")
        assert found.items[0].vat_rate == Decimal("5.5")

    def test_issuer_snapshot_immutability(self, session):
        """Spec §6: create doc with issuer snapshot, mutate company, reload doc — snapshot unchanged.

        Steps:
          1. Seed a company row with original legal_name.
          2. Create a billing document whose issuer_legal_name is copied from that company.
          3. UPDATE the company's legal_name in-place (simulates an admin rename).
          4. Reload the billing document.
          5. Assert the doc still carries the ORIGINAL legal_name — snapshot isolation confirmed.
        """
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingDocumentRepository(session)

        original_name = "Original Company SAS"
        mutated_name = "Renamed Company SARL"
        original_address = "1 rue de la Paix, 75001 Paris"
        mutated_address = "99 avenue des Champs, 75008 Paris"
        now = datetime.now(timezone.utc)

        # Seed company with original values
        company_row = CompanyModel(
            legal_name=original_name,
            address=original_address,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        session.add(company_row)
        session.flush()

        # Create billing document with snapshot of original issuer info
        doc = _make_doc(user_id, issuer_legal_name=original_name, issuer_address=original_address)
        saved = repo.save(doc)
        doc_id = UUID(str(saved.id))
        session.flush()

        # Mutate the company in-place (simulates admin renaming the company)
        company_row.legal_name = mutated_name
        company_row.address = mutated_address
        session.flush()

        # Reload the billing document and verify snapshot is unchanged
        reloaded = repo.find_by_id(doc_id)
        assert (
            reloaded.issuer_legal_name == original_name
        ), f"Expected snapshot to retain {original_name!r}, got {reloaded.issuer_legal_name!r}"
        assert (
            reloaded.issuer_address == original_address
        ), f"Expected snapshot to retain {original_address!r}, got {reloaded.issuer_address!r}"
