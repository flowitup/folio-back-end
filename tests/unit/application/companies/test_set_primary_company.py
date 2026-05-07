"""Unit tests for SetPrimaryCompanyUseCase.

Required regressions:
  test_set_primary_clears_previous
"""

from __future__ import annotations


import pytest

from app.application.companies.dtos import SetPrimaryCompanyInput
from app.application.companies.set_primary_company_usecase import SetPrimaryCompanyUseCase
from app.domain.companies.exceptions import UserCompanyAccessNotFoundError
from tests.unit.application.companies.conftest import make_access, make_company


@pytest.fixture
def usecase(access_repo):
    return SetPrimaryCompanyUseCase(access_repo=access_repo)


class TestSetPrimaryCompany:
    def test_set_primary_marks_target(
        self, usecase, access_repo, company_repo, seeded_company, user_id, admin_id, fake_session
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=False))
        inp = SetPrimaryCompanyInput(user_id=user_id, company_id=seeded_company.id)
        usecase.execute(inp, fake_session)
        access = access_repo.find(user_id, seeded_company.id)
        assert access.is_primary is True

    def test_set_primary_clears_previous(
        self, usecase, access_repo, company_repo, seeded_company, user_id, admin_id, fake_session
    ):
        """test_set_primary_clears_previous — required by spec.

        User has two attached companies A (primary) and B (not primary).
        After SetPrimary(B): A.is_primary=False AND B.is_primary=True.
        """
        company_b = make_company(created_by=admin_id)
        company_repo.save(company_b)

        # A = seeded_company (primary), B = company_b (not primary)
        access_a = make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True)
        access_b = make_access(user_id=user_id, company_id=company_b.id, is_primary=False)
        access_repo.save(access_a)
        access_repo.save(access_b)

        inp = SetPrimaryCompanyInput(user_id=user_id, company_id=company_b.id)
        usecase.execute(inp, fake_session)

        updated_a = access_repo.find(user_id, seeded_company.id)
        updated_b = access_repo.find(user_id, company_b.id)
        assert updated_a.is_primary is False
        assert updated_b.is_primary is True

    def test_not_attached_raises_not_found(self, usecase, seeded_company, user_id, fake_session):
        inp = SetPrimaryCompanyInput(user_id=user_id, company_id=seeded_company.id)
        with pytest.raises(UserCompanyAccessNotFoundError):
            usecase.execute(inp, fake_session)

    def test_at_most_one_primary_after_set(
        self, usecase, access_repo, company_repo, seeded_company, user_id, admin_id, fake_session
    ):
        """Invariant: exactly one primary per user after the call."""
        company_b = make_company(created_by=admin_id)
        company_c = make_company(created_by=admin_id)
        company_repo.save(company_b)
        company_repo.save(company_c)

        for c in [seeded_company, company_b, company_c]:
            access_repo.save(make_access(user_id=user_id, company_id=c.id, is_primary=False))

        inp = SetPrimaryCompanyInput(user_id=user_id, company_id=company_b.id)
        usecase.execute(inp, fake_session)

        primaries = [a for a in access_repo.list_for_user(user_id) if a.is_primary]
        assert len(primaries) == 1
        assert primaries[0].company_id == company_b.id
