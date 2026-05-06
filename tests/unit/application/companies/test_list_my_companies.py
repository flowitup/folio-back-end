"""Unit tests for ListMyCompaniesUseCase — masking applied for non-admin."""

from __future__ import annotations

import pytest

from app.application.companies.list_my_companies_usecase import ListMyCompaniesUseCase
from tests.unit.application.companies.conftest import make_access


@pytest.fixture
def usecase(company_repo, role_service):
    return ListMyCompaniesUseCase(company_repo=company_repo, role_checker=role_service)


class _CompanyRepoWithAccess:
    """Wrapper that supports list_attached_for_user via two repos."""

    def __init__(self, company_repo, access_repo):
        self._c = company_repo
        self._a = access_repo

    def list_attached_for_user(self, user_id):
        accesses = self._a.list_for_user(user_id)
        result = []
        for access in accesses:
            company = self._c.find_by_id(access.company_id)
            if company:
                result.append((company, access))
        return result

    # Delegate all other methods
    def __getattr__(self, name):
        return getattr(self._c, name)


@pytest.fixture
def usecase_with_access(company_repo, access_repo, role_service):
    """UseCase wired with a combined repo that implements list_attached_for_user."""
    combined = _CompanyRepoWithAccess(company_repo, access_repo)
    return ListMyCompaniesUseCase(company_repo=combined, role_checker=role_service)


class TestListMyCompanies:
    def test_returns_empty_for_unattached_user(self, usecase_with_access, user_id):
        result = usecase_with_access.execute(user_id)
        assert result.items == []

    def test_returns_attached_company(
        self, usecase_with_access, company_repo, access_repo, seeded_company, user_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        result = usecase_with_access.execute(user_id)
        assert len(result.items) == 1
        assert result.items[0].company.id == seeded_company.id

    def test_non_admin_siret_masked(
        self, usecase_with_access, company_repo, access_repo, seeded_company, user_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        result = usecase_with_access.execute(user_id)
        company_dto = result.items[0].company
        # siret="12345678901234" → masked to "····1234"
        assert company_dto.siret is None or "····" in (company_dto.siret or "")

    def test_admin_sees_full_siret(
        self, usecase_with_access, company_repo, access_repo, seeded_company, admin_id
    ):
        access_repo.save(make_access(user_id=admin_id, company_id=seeded_company.id))
        result = usecase_with_access.execute(admin_id)
        company_dto = result.items[0].company
        assert company_dto.siret == seeded_company.siret

    def test_is_primary_flag_in_result(
        self, usecase_with_access, company_repo, access_repo, seeded_company, user_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True))
        result = usecase_with_access.execute(user_id)
        assert result.items[0].access.is_primary is True
