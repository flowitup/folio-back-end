"""Unit tests for BootAttachedUserUseCase."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.boot_attached_user_usecase import BootAttachedUserUseCase
from app.application.companies.dtos import BootAttachedUserInput
from app.domain.companies.exceptions import (
    CompanyNotFoundError,
    ForbiddenCompanyError,
    UserCompanyAccessNotFoundError,
)
from tests.unit.application.companies.conftest import make_access


@pytest.fixture
def usecase(company_repo, access_repo, role_service):
    return BootAttachedUserUseCase(
        company_repo=company_repo,
        access_repo=access_repo,
        role_checker=role_service,
    )


class TestBootAttachedUser:
    def test_admin_boots_user(
        self, usecase, access_repo, seeded_company, admin_id, user_id, fake_session
    ):
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        inp = BootAttachedUserInput(
            caller_id=admin_id, company_id=seeded_company.id, target_user_id=user_id
        )
        usecase.execute(inp, fake_session)
        assert access_repo.find(user_id, seeded_company.id) is None

    def test_non_admin_raises_forbidden(
        self, usecase, seeded_company, user_id, fake_session
    ):
        inp = BootAttachedUserInput(
            caller_id=user_id, company_id=seeded_company.id, target_user_id=uuid4()
        )
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(inp, fake_session)

    def test_unknown_company_raises_not_found(
        self, usecase, admin_id, user_id, fake_session
    ):
        inp = BootAttachedUserInput(
            caller_id=admin_id, company_id=uuid4(), target_user_id=user_id
        )
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(inp, fake_session)

    def test_unattached_user_raises_not_found(
        self, usecase, seeded_company, admin_id, user_id, fake_session
    ):
        inp = BootAttachedUserInput(
            caller_id=admin_id, company_id=seeded_company.id, target_user_id=user_id
        )
        with pytest.raises(UserCompanyAccessNotFoundError):
            usecase.execute(inp, fake_session)
