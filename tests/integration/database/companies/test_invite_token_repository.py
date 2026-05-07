"""Integration tests for SqlAlchemyCompanyInviteTokenRepository against SQLite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.companies.company import Company
from app.domain.companies.invite_token import CompanyInviteToken
from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
    SqlAlchemyCompanyRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_company_invite_token_repository import (
    SqlAlchemyCompanyInviteTokenRepository,
)


def _make_company(session, created_by: UUID) -> Company:
    now = datetime.now(timezone.utc)
    company = Company(
        id=uuid4(),
        legal_name="Token Test SAS",
        address="1 rue Test",
        siret=None,
        tva_number=None,
        iban=None,
        bic=None,
        logo_url=None,
        default_payment_terms=None,
        prefix_override=None,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    return SqlAlchemyCompanyRepository(session).save(company)


def _make_token(company_id: UUID, created_by: UUID, days: int = 7, **overrides) -> CompanyInviteToken:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        company_id=company_id,
        token_hash="argon2_test_token",
        created_by=created_by,
        created_at=now,
        expires_at=now + timedelta(days=days),
        redeemed_at=None,
        redeemed_by=None,
    )
    defaults.update(overrides)
    return CompanyInviteToken(**defaults)


@pytest.fixture
def repo(session):
    return SqlAlchemyCompanyInviteTokenRepository(session)


@pytest.fixture
def creator_id():
    return uuid4()


class TestInviteTokenRepository:
    def test_save_and_find_by_id(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        token = _make_token(company_id=company.id, created_by=creator_id)
        repo.save(token)
        found = repo.find_by_id_for_update(token.id)
        assert found is not None
        assert found.id == token.id
        assert found.company_id == company.id

    def test_find_active_for_company_returns_active(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        token = _make_token(company_id=company.id, created_by=creator_id, days=7)
        repo.save(token)
        found = repo.find_active_for_company(company.id)
        assert found is not None
        assert found.id == token.id

    def test_find_active_for_company_returns_none_when_redeemed(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        redeemer = uuid4()
        now = datetime.now(timezone.utc)
        token = _make_token(
            company_id=company.id,
            created_by=creator_id,
            redeemed_at=now,
            redeemed_by=redeemer,
        )
        repo.save(token)
        found = repo.find_active_for_company(company.id)
        assert found is None

    def test_find_active_for_company_filters_redeemed_not_expiry(self, repo, session, creator_id):
        """find_active_for_company filters by redeemed_at=NULL only (not expires_at).
        Expiry check is the use-case's responsibility per repo docstring."""
        company = _make_company(session, creator_id)
        # An expired but NOT redeemed token is still returned by find_active_for_company
        token = _make_token(company_id=company.id, created_by=creator_id, days=-1)
        repo.save(token)
        # Repo returns it; use-case is responsible for checking expiry
        found = repo.find_active_for_company(company.id)
        assert found is not None
        # SQLite drops tzinfo; compare naively
        now_naive = datetime.utcnow()
        expires_naive = found.expires_at.replace(tzinfo=None) if found.expires_at.tzinfo else found.expires_at
        assert expires_naive < now_naive  # confirms the token was indeed created with days=-1

    def test_list_active_returns_only_active(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        active_token = _make_token(company_id=company.id, created_by=creator_id, days=7)
        now = datetime.now(timezone.utc)
        redeemed_token = _make_token(
            company_id=company.id,
            created_by=creator_id,
            token_hash="argon2_other",
            redeemed_at=now,
            redeemed_by=uuid4(),
        )
        repo.save(active_token)
        repo.save(redeemed_token)
        active = repo.list_active()
        active_ids = {t.id for t in active}
        assert active_token.id in active_ids
        assert redeemed_token.id not in active_ids

    def test_delete_removes_token(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        token = _make_token(company_id=company.id, created_by=creator_id)
        repo.save(token)
        repo.delete(token.id)
        assert repo.find_by_id_for_update(token.id) is None

    def test_save_updates_redeemed_fields(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        token = _make_token(company_id=company.id, created_by=creator_id)
        repo.save(token)
        now = datetime.now(timezone.utc)
        redeemer = uuid4()
        redeemed = token.with_updates(redeemed_at=now, redeemed_by=redeemer)
        repo.save(redeemed)
        found = repo.find_by_id_for_update(token.id)
        assert found.is_redeemed is True
        assert found.redeemed_by == redeemer
