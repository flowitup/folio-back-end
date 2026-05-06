"""Unit tests for UpdateCompanyUseCase."""

from __future__ import annotations

import pytest

from app.application.companies.dtos import UpdateCompanyInput
from app.application.companies.update_company_usecase import UpdateCompanyUseCase
from app.domain.companies.exceptions import CompanyNotFoundError, ForbiddenCompanyError


@pytest.fixture
def usecase(company_repo, role_service):
    return UpdateCompanyUseCase(company_repo=company_repo, role_checker=role_service)


class TestUpdateCompanyHappyPath:
    def test_admin_updates_legal_name(self, usecase, seeded_company, admin_id, fake_session):
        inp = UpdateCompanyInput(id=seeded_company.id, caller_id=admin_id, legal_name="New Name SAS")
        result = usecase.execute(inp, fake_session)
        assert result.legal_name == "New Name SAS"

    def test_partial_update_preserves_other_fields(self, usecase, seeded_company, admin_id, fake_session):
        original_address = seeded_company.address
        inp = UpdateCompanyInput(id=seeded_company.id, caller_id=admin_id, legal_name="Updated SAS")
        result = usecase.execute(inp, fake_session)
        assert result.address == original_address

    def test_update_sensitive_fields(self, usecase, seeded_company, admin_id, fake_session):
        inp = UpdateCompanyInput(
            id=seeded_company.id,
            caller_id=admin_id,
            siret="99999999901234",
            iban="FR76000",
        )
        result = usecase.execute(inp, fake_session)
        assert result.siret == "99999999901234"
        assert result.iban == "FR76000"

    def test_update_persisted(self, usecase, company_repo, seeded_company, admin_id, fake_session):
        inp = UpdateCompanyInput(id=seeded_company.id, caller_id=admin_id, legal_name="Stored Name SAS")
        usecase.execute(inp, fake_session)
        stored = company_repo.find_by_id(seeded_company.id)
        assert stored.legal_name == "Stored Name SAS"


class TestUpdateCompanyGuards:
    def test_non_admin_raises_forbidden(self, usecase, seeded_company, user_id, fake_session):
        inp = UpdateCompanyInput(id=seeded_company.id, caller_id=user_id, legal_name="X")
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(inp, fake_session)

    def test_unknown_company_raises_not_found(self, usecase, admin_id, fake_session):
        from uuid import uuid4
        inp = UpdateCompanyInput(id=uuid4(), caller_id=admin_id, legal_name="X")
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(inp, fake_session)

    def test_blank_legal_name_raises(self, usecase, seeded_company, admin_id, fake_session):
        inp = UpdateCompanyInput(id=seeded_company.id, caller_id=admin_id, legal_name="  ")
        with pytest.raises(ValueError):
            usecase.execute(inp, fake_session)
