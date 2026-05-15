"""Unit tests for SeedPaymentMethodsForCompanyUseCase."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.payment_methods.seed_payment_methods_for_company_usecase import (
    SeedPaymentMethodsForCompanyUseCase,
)
from .conftest import make_payment_method


@pytest.fixture
def usecase(pm_repo):
    return SeedPaymentMethodsForCompanyUseCase(payment_method_repo=pm_repo)


class TestSeedHappyPath:
    def test_inserts_cash_and_legal_name(self, usecase, pm_repo, fake_session):
        company_id = uuid4()
        caller_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=caller_id,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        labels = {m.label for m in methods}
        assert "Cash" in labels
        assert "Dupont SARL" in labels
        assert len(methods) == 2

    def test_all_seeded_methods_are_builtin(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert all(m.is_builtin for m in methods)

    def test_all_seeded_methods_are_active(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert all(m.is_active for m in methods)

    def test_legal_name_is_cash_inserts_only_one_row(self, usecase, pm_repo, fake_session):
        """legal_name == 'Cash' (case-insensitive) → only Cash inserted (no duplicate)."""
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Cash",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert len(methods) == 1
        assert methods[0].label == "Cash"

    def test_legal_name_cash_case_insensitive(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="CASH",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert len(methods) == 1

    def test_legal_name_none_inserts_only_cash(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name=None,
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert len(methods) == 1
        assert methods[0].label == "Cash"

    def test_legal_name_blank_inserts_only_cash(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="   ",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert len(methods) == 1
        assert methods[0].label == "Cash"


class TestSeedIdempotency:
    def test_skips_if_methods_already_exist(self, usecase, pm_repo, fake_session):
        company_id = uuid4()
        # Pre-seed one method
        pm_repo.save(make_payment_method(company_id, label="Existing"))

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        # Should still be only 1 — no additional rows inserted
        methods = pm_repo.find_all_by_company(company_id)
        assert len(methods) == 1
        assert methods[0].label == "Existing"

    def test_skips_even_if_existing_is_inactive(self, usecase, pm_repo, fake_session):
        """Idempotency guard triggers on ANY existing row, including inactive ones."""
        company_id = uuid4()
        pm_repo.save(make_payment_method(company_id, label="Old", is_active=False))

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        # include_inactive=True to verify no new rows
        all_methods = pm_repo.find_all_by_company(company_id, include_inactive=True)
        assert len(all_methods) == 1

    def test_created_by_propagated(self, usecase, pm_repo, fake_session):
        company_id = uuid4()
        caller_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=caller_id,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert all(m.created_by == caller_id for m in methods)

    def test_created_by_none_allowed(self, usecase, pm_repo, fake_session):
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert all(m.created_by is None for m in methods)
