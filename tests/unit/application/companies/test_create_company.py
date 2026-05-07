"""Unit tests for CreateCompanyUseCase."""

from __future__ import annotations


import pytest

from app.application.companies.create_company_usecase import CreateCompanyUseCase
from app.application.companies.dtos import CreateCompanyInput
from app.domain.companies.exceptions import ForbiddenCompanyError


@pytest.fixture
def usecase(company_repo, role_service):
    return CreateCompanyUseCase(company_repo=company_repo, role_checker=role_service)


def _inp(caller_id, **overrides):
    defaults = dict(
        caller_id=caller_id,
        legal_name="Test Corp SAS",
        address="1 rue de la Paix, 75001 Paris",
    )
    defaults.update(overrides)
    return CreateCompanyInput(**defaults)


class TestCreateCompanyHappyPath:
    def test_admin_creates_company(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        assert result.legal_name == "Test Corp SAS"
        assert result.address == "1 rue de la Paix, 75001 Paris"

    def test_company_persisted(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        stored = company_repo.find_by_id(result.id)
        assert stored is not None
        assert stored.legal_name == result.legal_name

    def test_optional_fields_passed_through(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(
            _inp(
                admin_id,
                siret="12345678901234",
                tva_number="FR12345678901",
                iban="FR76300",
                bic="BNPAFRPP",
                prefix_override="TST",
            ),
            fake_session,
        )
        assert result.siret == "12345678901234"
        assert result.prefix_override == "TST"

    def test_created_by_set_to_caller(self, usecase, company_repo, admin_id, fake_session):
        result = usecase.execute(_inp(admin_id), fake_session)
        stored = company_repo.find_by_id(result.id)
        assert stored.created_by == admin_id


class TestCreateCompanyGuards:
    def test_non_admin_raises_forbidden(self, usecase, user_id, fake_session):
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(_inp(user_id), fake_session)

    def test_blank_legal_name_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, legal_name="   "), fake_session)

    def test_blank_address_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, address=""), fake_session)

    def test_invalid_prefix_override_raises(self, usecase, admin_id, fake_session):
        with pytest.raises(ValueError):
            usecase.execute(_inp(admin_id, prefix_override="invalid!"), fake_session)
