"""Unit tests for CreatePaymentMethodUseCase."""

from __future__ import annotations

import pytest

from app.application.payment_methods.create_payment_method_usecase import CreatePaymentMethodUseCase
from app.application.payment_methods.dtos import CreatePaymentMethodInput
from app.domain.companies.exceptions import ForbiddenCompanyError
from app.domain.payment_methods.exceptions import PaymentMethodAlreadyExistsError
from .conftest import make_payment_method


@pytest.fixture
def usecase(pm_repo, role_service):
    return CreatePaymentMethodUseCase(payment_method_repo=pm_repo, role_checker=role_service)


def _inp(requester_id, company_id, label="Wire Transfer"):
    return CreatePaymentMethodInput(
        requester_id=requester_id,
        company_id=company_id,
        label=label,
    )


class TestCreatePaymentMethodHappyPath:
    def test_admin_creates_method(self, usecase, pm_repo, admin_id, company_id, fake_session):
        result = usecase.execute(_inp(admin_id, company_id, "Virement"), fake_session)

        assert result.label == "Virement"
        assert result.company_id == company_id
        assert result.is_active is True
        assert result.is_builtin is False

    def test_persisted_in_repo(self, usecase, pm_repo, admin_id, company_id, fake_session):
        result = usecase.execute(_inp(admin_id, company_id, "Virement"), fake_session)

        stored = pm_repo.find_by_id(result.id)
        assert stored is not None
        assert stored.label == "Virement"

    def test_created_by_set(self, usecase, pm_repo, admin_id, company_id, fake_session):
        result = usecase.execute(_inp(admin_id, company_id), fake_session)
        stored = pm_repo.find_by_id(result.id)
        assert stored.created_by == admin_id

    def test_label_is_stripped(self, usecase, pm_repo, admin_id, company_id, fake_session):
        result = usecase.execute(_inp(admin_id, company_id, "  Chèque  "), fake_session)
        assert result.label == "Chèque"


class TestCreatePaymentMethodGuards:
    def test_non_admin_raises_forbidden(self, usecase, user_id, company_id, fake_session):
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(_inp(user_id, company_id), fake_session)

    def test_whitespace_only_label_raises(self, usecase, admin_id, company_id, fake_session):
        with pytest.raises(ValueError, match="[Ll]abel"):
            usecase.execute(_inp(admin_id, company_id, "   "), fake_session)

    def test_empty_label_raises(self, usecase, admin_id, company_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, company_id, ""), fake_session)

    def test_label_exceeding_120_chars_raises(self, usecase, admin_id, company_id, fake_session):
        long_label = "A" * 121
        with pytest.raises(ValueError, match="120"):
            usecase.execute(_inp(admin_id, company_id, long_label), fake_session)

    def test_duplicate_label_exact_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm_repo.save(make_payment_method(company_id, label="Cash"))

        with pytest.raises(PaymentMethodAlreadyExistsError):
            usecase.execute(_inp(admin_id, company_id, "Cash"), fake_session)

    def test_duplicate_label_case_insensitive_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm_repo.save(make_payment_method(company_id, label="Cash"))

        with pytest.raises(PaymentMethodAlreadyExistsError):
            usecase.execute(_inp(admin_id, company_id, "CASH"), fake_session)

    def test_inactive_label_collision_allowed(self, usecase, pm_repo, admin_id, company_id, fake_session):
        # Soft-deleted row must not block re-creation with same label
        pm_repo.save(make_payment_method(company_id, label="Cash", is_active=False))

        result = usecase.execute(_inp(admin_id, company_id, "Cash"), fake_session)
        assert result.label == "Cash"

    def test_duplicate_in_other_company_allowed(self, usecase, pm_repo, admin_id, company_id, fake_session):
        from uuid import uuid4

        other = uuid4()
        pm_repo.save(make_payment_method(other, label="Cash"))

        result = usecase.execute(_inp(admin_id, company_id, "Cash"), fake_session)
        assert result.label == "Cash"
