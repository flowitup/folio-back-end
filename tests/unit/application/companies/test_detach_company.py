"""Unit tests for DetachCompanyUseCase."""

from __future__ import annotations


import pytest

from app.application.companies.detach_company_usecase import DetachCompanyUseCase
from app.application.companies.dtos import DetachCompanyInput
from app.domain.companies.exceptions import UserCompanyAccessNotFoundError
from tests.unit.application.companies.conftest import make_access, make_company


@pytest.fixture
def usecase(access_repo):
    return DetachCompanyUseCase(access_repo=access_repo)


class TestDetachCompany:
    def test_detach_removes_access(self, usecase, access_repo, seeded_company, user_id, fake_session):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True))
        inp = DetachCompanyInput(user_id=user_id, company_id=seeded_company.id)
        usecase.execute(inp, fake_session)
        assert access_repo.find(user_id, seeded_company.id) is None

    def test_detach_primary_auto_promotes_next(
        self, usecase, access_repo, company_repo, seeded_company, user_id, admin_id, fake_session
    ):
        """Auto-promote first remaining company when detaching primary."""
        company_b = make_company(created_by=admin_id)
        company_repo.save(company_b)

        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True))
        access_repo.save(make_access(user_id=user_id, company_id=company_b.id, is_primary=False))

        inp = DetachCompanyInput(user_id=user_id, company_id=seeded_company.id)
        usecase.execute(inp, fake_session)

        # seeded_company access gone
        assert access_repo.find(user_id, seeded_company.id) is None
        # company_b promoted to primary
        remaining = access_repo.find(user_id, company_b.id)
        assert remaining is not None
        assert remaining.is_primary is True

    def test_detach_non_primary_does_not_change_primary(
        self, usecase, access_repo, company_repo, seeded_company, user_id, admin_id, fake_session
    ):
        company_b = make_company(created_by=admin_id)
        company_repo.save(company_b)

        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True))
        access_repo.save(make_access(user_id=user_id, company_id=company_b.id, is_primary=False))

        # Detach the non-primary company_b
        inp = DetachCompanyInput(user_id=user_id, company_id=company_b.id)
        usecase.execute(inp, fake_session)

        # seeded_company still primary
        primary = access_repo.find(user_id, seeded_company.id)
        assert primary.is_primary is True

    def test_detach_last_company_leaves_no_primary(self, usecase, access_repo, seeded_company, user_id, fake_session):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id, is_primary=True))
        inp = DetachCompanyInput(user_id=user_id, company_id=seeded_company.id)
        usecase.execute(inp, fake_session)
        assert access_repo.list_for_user(user_id) == []

    def test_not_attached_raises_not_found(self, usecase, seeded_company, user_id, fake_session):
        inp = DetachCompanyInput(user_id=user_id, company_id=seeded_company.id)
        with pytest.raises(UserCompanyAccessNotFoundError):
            usecase.execute(inp, fake_session)
