"""Unit tests for ListPaymentMethodsUseCase."""

from __future__ import annotations

import pytest

from app.application.payment_methods.list_payment_methods_usecase import ListPaymentMethodsUseCase
from .conftest import make_payment_method


@pytest.fixture
def usecase(pm_repo, role_service):
    return ListPaymentMethodsUseCase(payment_method_repo=pm_repo, role_checker=role_service)


class TestListPaymentMethodsHappyPath:
    def test_returns_active_methods_by_default(self, usecase, pm_repo, admin_id, company_id):
        pm_repo.save(make_payment_method(company_id, label="Cash", is_active=True))
        pm_repo.save(make_payment_method(company_id, label="Wire Transfer", is_active=True))

        results = usecase.execute(requester_id=admin_id, company_id=company_id)

        assert len(results) == 2
        labels = [r.label for r in results]
        assert "Cash" in labels
        assert "Wire Transfer" in labels

    def test_excludes_inactive_by_default(self, usecase, pm_repo, admin_id, company_id):
        pm_repo.save(make_payment_method(company_id, label="Cash", is_active=True))
        pm_repo.save(make_payment_method(company_id, label="Deleted", is_active=False))

        results = usecase.execute(requester_id=admin_id, company_id=company_id)

        assert len(results) == 1
        assert results[0].label == "Cash"

    def test_admin_include_inactive_returns_all(self, usecase, pm_repo, admin_id, company_id):
        pm_repo.save(make_payment_method(company_id, label="Cash", is_active=True))
        pm_repo.save(make_payment_method(company_id, label="Deleted", is_active=False))

        results = usecase.execute(
            requester_id=admin_id,
            company_id=company_id,
            include_inactive=True,
        )

        assert len(results) == 2

    def test_non_admin_include_inactive_ignored(self, usecase, pm_repo, user_id, company_id):
        pm_repo.save(make_payment_method(company_id, label="Cash", is_active=True))
        pm_repo.save(make_payment_method(company_id, label="Deleted", is_active=False))

        # non-admin passes include_inactive=True but should still get active-only
        results = usecase.execute(
            requester_id=user_id,
            company_id=company_id,
            include_inactive=True,
        )

        assert len(results) == 1
        assert results[0].label == "Cash"

    def test_empty_company_returns_empty_list(self, usecase, admin_id, company_id):
        results = usecase.execute(requester_id=admin_id, company_id=company_id)
        assert results == []

    def test_usage_count_populated(self, usecase, pm_repo, admin_id, company_id):
        pm = make_payment_method(company_id, label="Cash")
        pm_repo.save(pm)
        pm_repo.set_invoice_count(pm.id, 5)

        results = usecase.execute(requester_id=admin_id, company_id=company_id)

        assert results[0].usage_count == 5

    def test_isolates_by_company(self, usecase, pm_repo, admin_id, company_id):
        from uuid import uuid4

        other_company = uuid4()
        pm_repo.save(make_payment_method(company_id, label="My Method"))
        pm_repo.save(make_payment_method(other_company, label="Other Company Method"))

        results = usecase.execute(requester_id=admin_id, company_id=company_id)

        assert len(results) == 1
        assert results[0].label == "My Method"

    def test_response_dto_fields(self, usecase, pm_repo, admin_id, company_id):
        pm = make_payment_method(company_id, label="Cash", is_builtin=True)
        pm_repo.save(pm)

        results = usecase.execute(requester_id=admin_id, company_id=company_id)

        r = results[0]
        assert r.id == pm.id
        assert r.company_id == pm.company_id
        assert r.label == "Cash"
        assert r.is_builtin is True
        assert r.is_active is True
