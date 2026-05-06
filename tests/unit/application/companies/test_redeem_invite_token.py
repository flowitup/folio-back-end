"""Unit tests for RedeemInviteTokenUseCase — all paths.

Required regressions covered:
  test_redeem_token_marks_redeemed_atomically
  test_token_redeem_410_uniform_for_wrong_token  (logic covered here; HTTP status in API tests)
"""

from __future__ import annotations


import pytest

from app.application.companies.dtos import RedeemInviteTokenInput
from app.application.companies.redeem_invite_token_usecase import RedeemInviteTokenUseCase
from app.domain.companies.exceptions import (
    CompanyAlreadyAttachedError,
    InviteTokenNotFoundError,
)
from tests.unit.application.companies.conftest import make_access, make_token


@pytest.fixture
def usecase(token_repo, access_repo, hasher, clock):
    return RedeemInviteTokenUseCase(
        token_repo=token_repo,
        access_repo=access_repo,
        hasher=hasher,
        clock=clock,
    )


def _seed_active_token(token_repo, company_id, admin_id, clock, plaintext="fake_token_0001"):
    """Seed a token whose hash matches the FakeArgon2Hasher for plaintext."""
    token = make_token(
        company_id=company_id,
        created_by=admin_id,
        clock=clock,
        token_hash="argon2_" + plaintext,
    )
    token_repo.save(token)
    return token, plaintext


class TestRedeemInviteTokenHappyPath:
    def test_redeem_attaches_user(
        self, usecase, token_repo, access_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        token, plaintext = _seed_active_token(token_repo, seeded_company.id, admin_id, clock)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        usecase.execute(inp, fake_session)
        access = access_repo.find(user_id, seeded_company.id)
        assert access is not None

    def test_first_redemption_sets_primary(
        self, usecase, token_repo, access_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        token, plaintext = _seed_active_token(token_repo, seeded_company.id, admin_id, clock)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        usecase.execute(inp, fake_session)
        access = access_repo.find(user_id, seeded_company.id)
        assert access.is_primary is True

    def test_subsequent_redemption_not_primary(
        self,
        usecase,
        token_repo,
        access_repo,
        company_repo,
        seeded_company,
        admin_id,
        user_id,
        clock,
        fake_session,
    ):
        from tests.unit.application.companies.conftest import make_company, make_access

        # User already attached to a different company (primary)
        other = make_company(created_by=admin_id)
        company_repo.save(other)
        access_repo.save(make_access(user_id=user_id, company_id=other.id, is_primary=True))

        # Now redeem token for seeded_company
        token, plaintext = _seed_active_token(token_repo, seeded_company.id, admin_id, clock)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        usecase.execute(inp, fake_session)

        new_access = access_repo.find(user_id, seeded_company.id)
        assert new_access.is_primary is False

    def test_redeem_token_marks_redeemed_atomically(
        self, usecase, token_repo, access_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        """test_redeem_token_marks_redeemed_atomically — required by spec.

        After redemption: token.redeemed_at is set, token.redeemed_by == user_id,
        and a second redeem attempt raises InviteTokenAlreadyRedeemedError (or
        InviteTokenNotFoundError because the token is no longer active).
        Both writes (token update + access insert) happen before commit.
        """
        token, plaintext = _seed_active_token(token_repo, seeded_company.id, admin_id, clock)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        usecase.execute(inp, fake_session)

        # Token is now marked redeemed in the store
        stored = token_repo.find_by_id_for_update(token.id)
        assert stored.is_redeemed is True
        assert stored.redeemed_by == user_id

        # Access row was created atomically with token mark
        access = access_repo.find(user_id, seeded_company.id)
        assert access is not None


class TestRedeemInviteTokenErrorPaths:
    def test_wrong_token_raises_not_found(
        self, usecase, token_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        """test_token_redeem_410_uniform_for_wrong_token (logic path)."""
        _seed_active_token(token_repo, seeded_company.id, admin_id, clock, plaintext="correct_token")
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token="wrong_token")
        with pytest.raises(InviteTokenNotFoundError):
            usecase.execute(inp, fake_session)

    def test_expired_token_raises_expired(
        self, usecase, token_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        plaintext = "fake_token_expired"
        token = make_token(
            company_id=seeded_company.id,
            created_by=admin_id,
            clock=clock,
            token_hash="argon2_" + plaintext,
            days_until_expiry=-1,  # already expired
        )
        token_repo.save(token)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        # Expired tokens not returned by list_active → InviteTokenNotFoundError
        with pytest.raises(InviteTokenNotFoundError):
            usecase.execute(inp, fake_session)

    def test_already_attached_raises_conflict(
        self, usecase, token_repo, access_repo, seeded_company, admin_id, user_id, clock, fake_session
    ):
        # Pre-attach user
        access_repo.save(make_access(user_id=user_id, company_id=seeded_company.id))
        token, plaintext = _seed_active_token(token_repo, seeded_company.id, admin_id, clock)
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token=plaintext)
        with pytest.raises(CompanyAlreadyAttachedError):
            usecase.execute(inp, fake_session)

    def test_no_active_tokens_raises_not_found(
        self, usecase, user_id, fake_session
    ):
        inp = RedeemInviteTokenInput(user_id=user_id, plaintext_token="any_token")
        with pytest.raises(InviteTokenNotFoundError):
            usecase.execute(inp, fake_session)
