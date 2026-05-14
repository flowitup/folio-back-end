"""Unit tests for UpdatePaymentMethodUseCase."""

from __future__ import annotations

import pytest

from app.application.payment_methods.update_payment_method_usecase import UpdatePaymentMethodUseCase
from app.application.payment_methods.dtos import UpdatePaymentMethodInput
from app.domain.companies.exceptions import ForbiddenCompanyError
from app.domain.payment_methods.exceptions import (
    BuiltinPaymentMethodDeletionError,
    PaymentMethodAlreadyExistsError,
    PaymentMethodNotFoundError,
)
from .conftest import make_payment_method


@pytest.fixture
def usecase(pm_repo, role_service):
    return UpdatePaymentMethodUseCase(payment_method_repo=pm_repo, role_checker=role_service)


def _inp(requester_id, pm_id, *, label=None, is_active=None):
    return UpdatePaymentMethodInput(
        requester_id=requester_id,
        payment_method_id=pm_id,
        label=label,
        is_active=is_active,
    )


class TestUpdatePaymentMethodHappyPath:
    def test_rename_happy_path(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Old Name")
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, label="New Name"), fake_session)

        assert result.label == "New Name"
        assert result.id == pm.id

    def test_rename_persisted(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Old Name")
        pm_repo.save(pm)

        usecase.execute(_inp(admin_id, pm.id, label="New Name"), fake_session)

        stored = pm_repo.find_by_id(pm.id)
        assert stored.label == "New Name"

    def test_deactivate_non_builtin_happy_path(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Wire", is_builtin=False)
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, is_active=False), fake_session)

        assert result.is_active is False

    def test_rename_builtin_allowed(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash", is_builtin=True)
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, label="Caisse"), fake_session)

        assert result.label == "Caisse"
        assert result.is_builtin is True

    def test_patch_only_payment_method_id_sets_both_columns(self, usecase, pm_repo, admin_id, company_id, fake_session):
        """Regression: PATCH with only label must carry is_active through unchanged."""
        pm = make_payment_method(company_id, label="Wire", is_active=True)
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, label="Wire Transfer"), fake_session)

        # Both columns must be set correctly — is_active unchanged
        assert result.label == "Wire Transfer"
        assert result.is_active is True

    def test_label_stripped(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash")
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, label="  Chèque  "), fake_session)

        assert result.label == "Chèque"

    def test_same_label_lowercase_no_collision(self, usecase, pm_repo, admin_id, company_id, fake_session):
        """Renaming to the same label (different case) on the same method is allowed."""
        pm = make_payment_method(company_id, label="Cash")
        pm_repo.save(pm)

        result = usecase.execute(_inp(admin_id, pm.id, label="CASH"), fake_session)

        assert result.label == "CASH"


class TestUpdatePaymentMethodGuards:
    def test_non_admin_raises_forbidden(self, usecase, pm_repo, user_id, company_id, fake_session):
        pm = make_payment_method(company_id)
        pm_repo.save(pm)

        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(_inp(user_id, pm.id, label="New"), fake_session)

    def test_not_found_raises(self, usecase, admin_id, company_id, fake_session):
        from uuid import uuid4

        with pytest.raises(PaymentMethodNotFoundError):
            usecase.execute(_inp(admin_id, uuid4(), label="X"), fake_session)

    def test_rename_to_existing_label_raises_409(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm_repo.save(make_payment_method(company_id, label="Cash"))
        wire = make_payment_method(company_id, label="Wire")
        pm_repo.save(wire)

        with pytest.raises(PaymentMethodAlreadyExistsError):
            usecase.execute(_inp(admin_id, wire.id, label="Cash"), fake_session)

    def test_deactivate_builtin_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash", is_builtin=True)
        pm_repo.save(pm)

        with pytest.raises(BuiltinPaymentMethodDeletionError):
            usecase.execute(_inp(admin_id, pm.id, is_active=False), fake_session)

    def test_rename_to_existing_label_ci_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm_repo.save(make_payment_method(company_id, label="Cash"))
        wire = make_payment_method(company_id, label="Wire")
        pm_repo.save(wire)

        with pytest.raises(PaymentMethodAlreadyExistsError):
            usecase.execute(_inp(admin_id, wire.id, label="CASH"), fake_session)

    def test_whitespace_only_label_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash")
        pm_repo.save(pm)

        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, pm.id, label="   "), fake_session)

    def test_label_exceeding_120_chars_raises(self, usecase, pm_repo, admin_id, company_id, fake_session):
        pm = make_payment_method(company_id, label="Cash")
        pm_repo.save(pm)

        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, pm.id, label="A" * 121), fake_session)
