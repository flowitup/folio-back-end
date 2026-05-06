"""Unit tests for RevokeInviteTokenUseCase."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.revoke_invite_token_usecase import RevokeInviteTokenUseCase
from app.domain.companies.exceptions import (
    CompanyNotFoundError,
    ForbiddenCompanyError,
    InviteTokenNotFoundError,
)
from tests.unit.application.companies.conftest import make_token


@pytest.fixture
def usecase(company_repo, token_repo, role_service):
    return RevokeInviteTokenUseCase(
        company_repo=company_repo,
        token_repo=token_repo,
        role_checker=role_service,
    )


class TestRevokeInviteTokenHappyPath:
    def test_revokes_active_token(self, usecase, token_repo, seeded_company, admin_id, clock, fake_session):
        token = make_token(company_id=seeded_company.id, created_by=admin_id, clock=clock)
        token_repo.save(token)

        usecase.execute(admin_id, seeded_company.id, fake_session)

        assert token_repo.find_by_id_for_update(token.id) is None

    def test_no_active_token_raises_not_found(self, usecase, seeded_company, admin_id, fake_session):
        with pytest.raises(InviteTokenNotFoundError):
            usecase.execute(admin_id, seeded_company.id, fake_session)


class TestRevokeInviteTokenGuards:
    def test_non_admin_raises_forbidden(self, usecase, seeded_company, user_id, fake_session):
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(user_id, seeded_company.id, fake_session)

    def test_unknown_company_raises_not_found(self, usecase, admin_id, fake_session):
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(admin_id, uuid4(), fake_session)
