"""Unit tests: company-payment flag on seeded payment methods.

When a company is seeded with a legal name:
- the legal-name builtin gets is_company_payment=True
- the Cash builtin stays is_company_payment=False
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.payment_methods.seed_payment_methods_for_company_usecase import (
    SeedPaymentMethodsForCompanyUseCase,
)


@pytest.fixture
def usecase(pm_repo):
    return SeedPaymentMethodsForCompanyUseCase(payment_method_repo=pm_repo)


class TestCompanyPaymentFlagOnSeed:
    def test_legal_name_builtin_is_company_payment(self, usecase, pm_repo, fake_session):
        """The legal-name builtin is flagged is_company_payment=True after seeding."""
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        legal_name_method = next(m for m in methods if m.label == "Dupont SARL")
        assert legal_name_method.is_company_payment is True

    def test_cash_builtin_is_not_company_payment(self, usecase, pm_repo, fake_session):
        """The Cash builtin must have is_company_payment=False after seeding."""
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        cash_method = next(m for m in methods if m.label == "Cash")
        assert cash_method.is_company_payment is False

    def test_only_cash_no_company_payment_flag(self, usecase, pm_repo, fake_session):
        """When only Cash is seeded (no legal name), it is not flagged."""
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
        assert methods[0].is_company_payment is False
