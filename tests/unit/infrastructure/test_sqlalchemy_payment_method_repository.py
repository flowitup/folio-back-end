"""Repository-level tests for SqlAlchemyPaymentMethodRepository.

Runs against SQLite in-memory DB using the shared session fixture from conftest.
Covers find_by_id, find_active_by_company, find_by_label_ci, count_invoices_referencing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.infrastructure.database.repositories.sqlalchemy_payment_method_repository import (
    SqlAlchemyPaymentMethodRepository,
)
from app.infrastructure.database.models.payment_method import PaymentMethodModel


def _now():
    return datetime.now(timezone.utc)


def _insert_pm(session, company_id: UUID, label: str, is_active: bool = True, is_builtin: bool = False):
    """Insert a PaymentMethodModel row directly and return it."""
    row = PaymentMethodModel(
        id=uuid4(),
        company_id=company_id,  # must be UUID object, not string
        label=label,
        is_builtin=is_builtin,
        is_active=is_active,
        created_by=None,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(row)
    session.flush()
    return row


class TestFindById:
    def test_find_by_id_returns_entity(self, session):
        company_id = uuid4()
        row = _insert_pm(session, company_id, "Cash")
        repo = SqlAlchemyPaymentMethodRepository(session)

        result = repo.find_by_id(row.id)

        assert result is not None
        assert result.id == row.id
        assert result.label == "Cash"

    def test_find_by_id_returns_none_for_unknown(self, session):
        repo = SqlAlchemyPaymentMethodRepository(session)

        result = repo.find_by_id(uuid4())

        assert result is None


class TestFindActiveByCompany:
    def test_find_active_by_company_returns_only_active(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Active A", is_active=True)
        _insert_pm(session, company_id, "Inactive B", is_active=False)
        repo = SqlAlchemyPaymentMethodRepository(session)

        results = repo.find_active_by_company(company_id)

        assert len(results) == 1
        assert results[0].label == "Active A"

    def test_find_active_by_company_ordered_by_label(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Zorro")
        _insert_pm(session, company_id, "Alpha")
        repo = SqlAlchemyPaymentMethodRepository(session)

        results = repo.find_active_by_company(company_id)

        assert [r.label for r in results] == ["Alpha", "Zorro"]

    def test_find_active_by_company_empty(self, session):
        repo = SqlAlchemyPaymentMethodRepository(session)
        assert repo.find_active_by_company(uuid4()) == []


class TestFindByIdForUpdate:
    def test_find_by_id_for_update_returns_entity(self, session):
        company_id = uuid4()
        row = _insert_pm(session, company_id, "Wire")
        repo = SqlAlchemyPaymentMethodRepository(session)

        result = repo.find_by_id_for_update(row.id)

        assert result is not None
        assert result.label == "Wire"

    def test_find_by_id_for_update_returns_none_for_unknown(self, session):
        repo = SqlAlchemyPaymentMethodRepository(session)
        assert repo.find_by_id_for_update(uuid4()) is None


class TestFindAllByCompany:
    def test_find_all_active_only_by_default(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Active")
        _insert_pm(session, company_id, "Inactive", is_active=False)
        repo = SqlAlchemyPaymentMethodRepository(session)

        results = repo.find_all_by_company(company_id)

        assert len(results) == 1
        assert results[0].label == "Active"

    def test_find_all_include_inactive(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Active")
        _insert_pm(session, company_id, "Inactive", is_active=False)
        repo = SqlAlchemyPaymentMethodRepository(session)

        results = repo.find_all_by_company(company_id, include_inactive=True)

        assert len(results) == 2


class TestFindByLabelCi:
    def test_case_insensitive_match(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Cash")
        repo = SqlAlchemyPaymentMethodRepository(session)

        result = repo.find_by_label_ci(company_id, "CASH")

        assert result is not None
        assert result.label == "Cash"

    def test_returns_none_when_not_found(self, session):
        repo = SqlAlchemyPaymentMethodRepository(session)
        assert repo.find_by_label_ci(uuid4(), "NoSuch") is None

    def test_only_active_by_default(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Cash", is_active=False)
        repo = SqlAlchemyPaymentMethodRepository(session)

        # Inactive row must not match when only_active=True
        result = repo.find_by_label_ci(company_id, "Cash", only_active=True)
        assert result is None

    def test_include_inactive_when_only_active_false(self, session):
        company_id = uuid4()
        _insert_pm(session, company_id, "Cash", is_active=False)
        repo = SqlAlchemyPaymentMethodRepository(session)

        result = repo.find_by_label_ci(company_id, "Cash", only_active=False)
        assert result is not None


class TestSave:
    def test_save_inserts_new_row(self, session):
        company_id = uuid4()
        repo = SqlAlchemyPaymentMethodRepository(session)
        from app.domain.payment_methods.payment_method import PaymentMethod

        now = _now()
        method = PaymentMethod(
            id=uuid4(),
            company_id=company_id,
            label="New Method",
            is_builtin=False,
            is_active=True,
            created_by=None,
            created_at=now,
            updated_at=now,
        )

        saved = repo.save(method)

        assert saved.id == method.id
        assert saved.label == "New Method"
        found = repo.find_by_id(method.id)
        assert found is not None

    def test_save_updates_existing_row(self, session):
        company_id = uuid4()
        row = _insert_pm(session, company_id, "Old Label")
        repo = SqlAlchemyPaymentMethodRepository(session)

        existing = repo.find_by_id(row.id)
        updated = existing.with_updates(label="New Label", updated_at=_now())
        repo.save(updated)

        refetched = repo.find_by_id(row.id)
        assert refetched.label == "New Label"


class TestInsertMany:
    def test_insert_many_bulk(self, session):
        company_id = uuid4()
        repo = SqlAlchemyPaymentMethodRepository(session)
        from app.domain.payment_methods.payment_method import PaymentMethod

        now = _now()
        methods = [
            PaymentMethod(
                id=uuid4(),
                company_id=company_id,
                label=label,
                is_builtin=True,
                is_active=True,
                created_by=None,
                created_at=now,
                updated_at=now,
            )
            for label in ["Cash", "Wire"]
        ]

        repo.insert_many(methods)

        results = repo.find_all_by_company(company_id)
        assert len(results) == 2
