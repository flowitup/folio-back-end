"""Unit tests: company-payment flag on the seeded payment method.

The company's legal name is the only builtin seeded, and it is flagged
is_company_payment=True — invoices settled through it are funded by the
company and count toward "spent by company".
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

    def test_seeded_builtin_is_not_personal_payment(self, usecase, pm_repo, fake_session):
        """is_company_payment and is_personal_payment are mutually exclusive."""
        company_id = uuid4()

        usecase.execute(
            company_id=company_id,
            legal_name="Dupont SARL",
            created_by=None,
            db_session=fake_session,
        )

        methods = pm_repo.find_all_by_company(company_id)
        assert all(m.is_personal_payment is False for m in methods)
