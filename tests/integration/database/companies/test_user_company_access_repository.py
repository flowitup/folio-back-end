"""Integration tests for SqlAlchemyUserCompanyAccessRepository against SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.companies.company import Company
from app.domain.companies.user_company_access import UserCompanyAccess
from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
    SqlAlchemyCompanyRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_user_company_access_repository import (
    SqlAlchemyUserCompanyAccessRepository,
)


def _make_company(session, created_by: UUID, legal_name: str = "Test SAS") -> Company:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    company = Company(
        id=uuid4(),
        legal_name=legal_name,
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
    repo = SqlAlchemyCompanyRepository(session)
    return repo.save(company)


def _make_access(user_id: UUID, company_id: UUID, is_primary: bool = False) -> UserCompanyAccess:
    return UserCompanyAccess(
        user_id=user_id,
        company_id=company_id,
        is_primary=is_primary,
        attached_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def repo(session):
    return SqlAlchemyUserCompanyAccessRepository(session)


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def creator_id():
    return uuid4()


class TestUserCompanyAccessRepository:
    def test_save_and_find(self, repo, session, user_id, creator_id):
        company = _make_company(session, creator_id)
        access = _make_access(user_id=user_id, company_id=company.id, is_primary=True)
        repo.save(access)
        found = repo.find(user_id, company.id)
        assert found is not None
        assert found.user_id == user_id
        assert found.company_id == company.id
        assert found.is_primary is True

    def test_find_returns_none_for_missing(self, repo, user_id):
        assert repo.find(user_id, uuid4()) is None

    def test_delete_removes_access(self, repo, session, user_id, creator_id):
        company = _make_company(session, creator_id)
        access = _make_access(user_id=user_id, company_id=company.id)
        repo.save(access)
        repo.delete(user_id, company.id)
        assert repo.find(user_id, company.id) is None

    def test_list_for_user(self, repo, session, user_id, creator_id):
        c1 = _make_company(session, creator_id, "Company A")
        c2 = _make_company(session, creator_id, "Company B")
        repo.save(_make_access(user_id=user_id, company_id=c1.id, is_primary=True))
        repo.save(_make_access(user_id=user_id, company_id=c2.id, is_primary=False))
        accesses = repo.list_for_user(user_id)
        assert len(accesses) == 2

    def test_list_for_company(self, repo, session, creator_id):
        company = _make_company(session, creator_id)
        uid1, uid2 = uuid4(), uuid4()
        repo.save(_make_access(user_id=uid1, company_id=company.id))
        repo.save(_make_access(user_id=uid2, company_id=company.id))
        accesses = repo.list_for_company(company.id)
        assert len(accesses) == 2

    def test_clear_primary_for_user(self, repo, session, user_id, creator_id):
        c1 = _make_company(session, creator_id, "Primary Corp")
        c2 = _make_company(session, creator_id, "Other Corp")
        repo.save(_make_access(user_id=user_id, company_id=c1.id, is_primary=True))
        repo.save(_make_access(user_id=user_id, company_id=c2.id, is_primary=False))
        repo.clear_primary_for_user(user_id)
        accesses = repo.list_for_user(user_id)
        assert all(not a.is_primary for a in accesses)

    def test_update_is_primary_via_save(self, repo, session, user_id, creator_id):
        company = _make_company(session, creator_id)
        access = _make_access(user_id=user_id, company_id=company.id, is_primary=False)
        repo.save(access)
        updated = access.with_updates(is_primary=True)
        repo.save(updated)
        found = repo.find(user_id, company.id)
        assert found.is_primary is True
