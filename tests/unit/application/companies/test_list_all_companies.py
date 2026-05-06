"""Unit tests for ListAllCompaniesUseCase — admin-only."""

from __future__ import annotations

import pytest

from app.application.companies.dtos import ListAllCompaniesInput
from app.application.companies.list_all_companies_usecase import ListAllCompaniesUseCase
from app.domain.companies.exceptions import ForbiddenCompanyError
from tests.unit.application.companies.conftest import make_company


@pytest.fixture
def usecase(company_repo, role_service):
    return ListAllCompaniesUseCase(company_repo=company_repo, role_checker=role_service)


class TestListAllCompanies:
    def test_admin_lists_all(self, usecase, company_repo, admin_id):
        for _ in range(3):
            company_repo.save(make_company(created_by=admin_id))
        inp = ListAllCompaniesInput(caller_id=admin_id, limit=50, offset=0)
        result = usecase.execute(inp)
        assert len(result.items) == 3
        assert result.total == 3

    def test_pagination_limit(self, usecase, company_repo, admin_id):
        for _ in range(5):
            company_repo.save(make_company(created_by=admin_id))
        inp = ListAllCompaniesInput(caller_id=admin_id, limit=2, offset=0)
        result = usecase.execute(inp)
        assert len(result.items) == 2
        assert result.total == 5

    def test_pagination_offset(self, usecase, company_repo, admin_id):
        for _ in range(5):
            company_repo.save(make_company(created_by=admin_id))
        inp = ListAllCompaniesInput(caller_id=admin_id, limit=50, offset=3)
        result = usecase.execute(inp)
        assert len(result.items) == 2

    def test_non_admin_raises_forbidden(self, usecase, user_id):
        inp = ListAllCompaniesInput(caller_id=user_id, limit=50, offset=0)
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(inp)

    def test_empty_returns_empty_list(self, usecase, admin_id):
        inp = ListAllCompaniesInput(caller_id=admin_id, limit=50, offset=0)
        result = usecase.execute(inp)
        assert result.items == []
        assert result.total == 0
