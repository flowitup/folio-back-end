"""Unit tests for PaymentMethod domain entity."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.domain.payment_methods.payment_method import PaymentMethod


def _make_pm(**overrides) -> PaymentMethod:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        company_id=uuid4(),
        label="Cash",
        is_builtin=False,
        is_active=True,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return PaymentMethod(**defaults)


class TestWithUpdates:
    def test_with_updates_label_returns_new_instance(self):
        pm = _make_pm(label="Old Label")
        updated = pm.with_updates(label="New Label")
        assert updated.label == "New Label"
        assert pm.label == "Old Label"  # original unchanged (frozen)

    def test_with_updates_label_updated_at_newer(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = datetime(2026, 1, 2, tzinfo=timezone.utc)
        pm = _make_pm(label="Old", updated_at=now)
        updated = pm.with_updates(label="New", updated_at=later)
        assert updated.updated_at == later
        assert updated.updated_at > pm.updated_at

    def test_with_updates_is_active_false(self):
        pm = _make_pm(is_active=True)
        updated = pm.with_updates(is_active=False)
        assert updated.is_active is False
        assert pm.is_active is True

    def test_with_updates_carries_over_unchanged_fields(self):
        company_id = uuid4()
        pm = _make_pm(company_id=company_id, label="Cash", is_builtin=True)
        updated = pm.with_updates(label="Espèces")
        assert updated.company_id == company_id
        assert updated.is_builtin is True
        assert updated.id == pm.id

    def test_with_updates_no_args_returns_identical(self):
        pm = _make_pm()
        updated = pm.with_updates()
        assert updated == pm
        assert updated is not pm  # new instance

    def test_with_updates_partial_fields(self):
        pm = _make_pm(label="Cash", is_active=True)
        updated = pm.with_updates(is_active=False)
        assert updated.label == "Cash"
        assert updated.is_active is False


class TestEquality:
    def test_same_id_equal(self):
        id_ = uuid4()
        pm1 = _make_pm(id=id_, label="A")
        pm2 = _make_pm(id=id_, label="B")
        assert pm1 == pm2

    def test_different_id_not_equal(self):
        pm1 = _make_pm()
        pm2 = _make_pm()
        assert pm1 != pm2

    def test_equality_not_implemented_for_other_types(self):
        pm = _make_pm()
        result = pm.__eq__("not a payment method")
        assert result is NotImplemented

    def test_equality_not_implemented_for_none(self):
        pm = _make_pm()
        result = pm.__eq__(None)
        assert result is NotImplemented


class TestHashing:
    def test_hash_based_on_id(self):
        id_ = uuid4()
        pm1 = _make_pm(id=id_)
        pm2 = _make_pm(id=id_)
        assert hash(pm1) == hash(pm2)

    def test_usable_in_set(self):
        id_ = uuid4()
        pm1 = _make_pm(id=id_)
        pm2 = _make_pm(id=id_)
        assert len({pm1, pm2}) == 1

    def test_different_ids_different_hash(self):
        pm1 = _make_pm()
        pm2 = _make_pm()
        assert hash(pm1) != hash(pm2)
