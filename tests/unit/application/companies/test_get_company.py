"""Unit tests for GetCompanyUseCase — 404-not-403 leak guard."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.dtos import GetCompanyInput
from app.application.companies.get_company_usecase import GetCompanyUseCase
from app.domain.companies.exceptions import CompanyNotFoundError
from tests.unit.application.companies.conftest import make_access


@pytest.fixture
def usecase(company_repo, access_repo, role_service):
    return GetCompanyUseCase(
        company_repo=company_repo,
        access_repo=access_repo,
        role_checker=role_service,
    )


class TestGetCompany:
    def test_attached_user_gets_company(
        self, usecase, access_repo, seeded_company, user_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        inp = GetCompanyInput(caller_id=user_id, company_id=seeded_company.id)
        result = usecase.execute(inp)
        assert result.id == seeded_company.id

    def test_non_attached_user_gets_404_not_403(
        self, usecase, seeded_company, user_id
    ):
        """Spec: returns 404 (not 403) to avoid leaking company existence."""
        inp = GetCompanyInput(caller_id=user_id, company_id=seeded_company.id)
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(inp)

    def test_admin_gets_any_company(
        self, usecase, seeded_company, admin_id
    ):
        """Admin can retrieve any company regardless of attachment."""
        inp = GetCompanyInput(caller_id=admin_id, company_id=seeded_company.id)
        result = usecase.execute(inp)
        assert result.id == seeded_company.id

    def test_unknown_company_returns_404(self, usecase, admin_id):
        inp = GetCompanyInput(caller_id=admin_id, company_id=uuid4())
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(inp)

    def test_non_admin_sensitive_fields_masked(
        self, usecase, access_repo, seeded_company, user_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        inp = GetCompanyInput(caller_id=user_id, company_id=seeded_company.id)
        result = usecase.execute(inp)
        assert result.siret is None or "····" in (result.siret or "")

    def test_admin_sees_full_sensitive_fields(
        self, usecase, seeded_company, admin_id
    ):
        inp = GetCompanyInput(caller_id=admin_id, company_id=seeded_company.id)
        result = usecase.execute(inp)
        assert result.siret == seeded_company.siret
