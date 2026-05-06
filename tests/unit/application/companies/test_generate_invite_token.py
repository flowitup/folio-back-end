"""Unit tests for GenerateInviteTokenUseCase."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.companies.dtos import GenerateInviteTokenInput
from app.application.companies.generate_invite_token_usecase import GenerateInviteTokenUseCase
from app.domain.companies.exceptions import (
    ActiveInviteTokenAlreadyExistsError,
    CompanyNotFoundError,
    ForbiddenCompanyError,
)


@pytest.fixture
def usecase(company_repo, token_repo, hasher, token_generator, clock, role_service):
    return GenerateInviteTokenUseCase(
        company_repo=company_repo,
        token_repo=token_repo,
        hasher=hasher,
        token_generator=token_generator,
        clock=clock,
        role_checker=role_service,
    )


class TestGenerateInviteTokenHappyPath:
    def test_returns_plaintext_token(self, usecase, seeded_company, admin_id, fake_session):
        inp = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id)
        result = usecase.execute(inp, fake_session)
        assert result.plaintext_token.startswith("fake_token_")

    def test_token_persisted_as_hash(self, usecase, token_repo, seeded_company, admin_id, fake_session):
        inp = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id)
        result = usecase.execute(inp, fake_session)
        stored = token_repo.find_by_id_for_update(result.token_id)
        assert stored is not None
        assert stored.token_hash == "argon2_" + result.plaintext_token

    def test_expires_at_set_7_days_out(self, usecase, seeded_company, admin_id, clock, fake_session):
        from datetime import timedelta
        inp = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id)
        result = usecase.execute(inp, fake_session)
        expected = clock.now() + timedelta(days=7)
        assert result.expires_at == expected

    def test_regenerate_replaces_existing_token(
        self, usecase, token_repo, seeded_company, admin_id, clock, fake_session
    ):
        # Generate first token
        inp1 = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id)
        r1 = usecase.execute(inp1, fake_session)
        # Regenerate
        inp2 = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id, regenerate=True)
        r2 = usecase.execute(inp2, fake_session)
        assert r1.token_id != r2.token_id
        # Old token deleted
        assert token_repo.find_by_id_for_update(r1.token_id) is None


class TestGenerateInviteTokenConflict:
    def test_second_generate_without_regenerate_raises(
        self, usecase, seeded_company, admin_id, fake_session
    ):
        inp = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=admin_id)
        usecase.execute(inp, fake_session)
        with pytest.raises(ActiveInviteTokenAlreadyExistsError):
            usecase.execute(inp, fake_session)


class TestGenerateInviteTokenGuards:
    def test_non_admin_raises_forbidden(self, usecase, seeded_company, user_id, fake_session):
        inp = GenerateInviteTokenInput(company_id=seeded_company.id, caller_id=user_id)
        with pytest.raises(ForbiddenCompanyError):
            usecase.execute(inp, fake_session)

    def test_unknown_company_raises_not_found(self, usecase, admin_id, fake_session):
        inp = GenerateInviteTokenInput(company_id=uuid4(), caller_id=admin_id)
        with pytest.raises(CompanyNotFoundError):
            usecase.execute(inp, fake_session)
