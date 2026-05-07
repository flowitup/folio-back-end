"""Unit tests for Company domain entity."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.companies.company import Company


def _make_company(**overrides) -> Company:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        legal_name="ACME SAS",
        address="1 rue de la Paix, 75001 Paris",
        siret="12345678901234",
        tva_number="FR12345678901",
        iban="FR7630001007941234567890185",
        bic="BNPAFRPP",
        logo_url=None,
        default_payment_terms="30 days",
        prefix_override="ACM",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Company(**defaults)


class TestCompanyWithUpdates:
    def test_with_updates_returns_new_instance(self):
        c = _make_company()
        updated = c.with_updates(legal_name="New Name SAS")
        assert updated is not c

    def test_with_updates_changes_target_field(self):
        c = _make_company(legal_name="Old Name")
        updated = c.with_updates(legal_name="New Name SAS")
        assert updated.legal_name == "New Name SAS"

    def test_with_updates_preserves_other_fields(self):
        c = _make_company(address="42 rue Rivoli")
        updated = c.with_updates(legal_name="New Name SAS")
        assert updated.address == "42 rue Rivoli"
        assert updated.id == c.id

    def test_with_updates_frozen_original(self):
        c = _make_company()
        c.with_updates(legal_name="X")
        assert c.legal_name == "ACME SAS"  # original unchanged

    def test_with_updates_multiple_fields(self):
        c = _make_company()
        updated = c.with_updates(siret=None, tva_number=None)
        assert updated.siret is None
        assert updated.tva_number is None
        assert updated.id == c.id


class TestCompanyEquality:
    def test_equal_same_id(self):
        c_id = uuid4()
        c1 = _make_company(id=c_id, legal_name="A")
        c2 = _make_company(id=c_id, legal_name="B")
        assert c1 == c2

    def test_not_equal_different_id(self):
        c1 = _make_company()
        c2 = _make_company()
        assert c1 != c2

    def test_hash_consistent_with_equality(self):
        c_id = uuid4()
        c1 = _make_company(id=c_id)
        c2 = _make_company(id=c_id)
        assert hash(c1) == hash(c2)

    def test_not_equal_to_non_company(self):
        c = _make_company()
        assert c.__eq__("not a company") is NotImplemented
