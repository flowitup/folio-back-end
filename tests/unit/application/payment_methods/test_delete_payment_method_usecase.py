"""Unit tests for DeletePaymentMethodUseCase."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.payment_methods.delete_payment_method_usecase import DeletePaymentMethodUseCase
from app.domain.companies.exceptions import ForbiddenCompanyError
from app.domain.payment_methods.exceptions import (
    BuiltinPaymentMethodDeletionError,
    PaymentMethodNotFoundError,
)
from .conftest import make_payment_method


@pytest.fixture
def usecase(pm_repo, role_service):
    return DeletePaymentMethodUseCase(payment_method_repo=pm_repo, role_checker=role_service)


class TestDeletePaymentMethodHappyPath:
    def test_soft_delete_active_method(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Wire", is_active=True)
        pm_repo.save(pm)

        usecase.execute(
            requester_id=admin_id,
            payment_method_id=pm.id,
            db_session=fake_session,
            company_id=company_id,
        )

        stored = pm_repo.find_by_id(pm.id)
        assert stored is not None
        assert stored.is_active is False

    def test_returns_none(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Wire")
        pm_repo.save(pm)

        result = usecase.execute(
            requester_id=admin_id,
            payment_method_id=pm.id,
            db_session=fake_session,
            company_id=company_id,
        )

        assert result is None

    def test_idempotent_already_inactive(self, usecase, pm_repo, admin_id, company_id, fake_session):
        """Calling delete on an already-inactive method is a no-op (idempotent)."""
        pm = make_payment_method(company_id, label="Wire", is_active=False)
        pm_repo.save(pm)

        # Should not raise — already inactive is fine
        usecase.execute(
            requester_id=admin_id,
            payment_method_id=pm.id,
            db_session=fake_session,
            company_id=company_id,
        )

        stored = pm_repo.find_by_id(pm.id)
        assert stored.is_active is False


class TestDeletePaymentMethodGuards:
    def test_non_admin_raises_forbidden(self, usecase, pm_repo, user_id, company_id, fake_session):
        pm = make_payment_method(company_id)
        pm_repo.save(pm)

        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(
                requester_id=user_id,
                payment_method_id=pm.id,
                db_session=fake_session,
                company_id=company_id,
            )

    def test_not_found_raises(self, usecase, admin_id, company_id, fake_session):
        with pytest.raises(PaymentMethodNotFoundError):
            usecase.execute(
                requester_id=admin_id,
                payment_method_id=uuid4(),
                db_session=fake_session,
                company_id=company_id,
            )

    def test_builtin_raises_409(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash", is_builtin=True)
        pm_repo.save(pm)

        with pytest.raises(BuiltinPaymentMethodDeletionError):
            usecase.execute(
                requester_id=admin_id,
                payment_method_id=pm.id,
                db_session=fake_session,
                company_id=company_id,
            )

    def test_builtin_checked_before_active_status(self, usecase, pm_repo, admin_id, company_id, fake_session):
        """Builtin guard fires even if method is already inactive."""
        pm = make_payment_method(company_id, label="Cash", is_builtin=True, is_active=True)
        pm_repo.save(pm)

        with pytest.raises(BuiltinPaymentMethodDeletionError):
            usecase.execute(
                requester_id=admin_id,
                payment_method_id=pm.id,
                db_session=fake_session,
                company_id=company_id,
            )
