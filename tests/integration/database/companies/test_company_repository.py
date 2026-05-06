"""Integration tests for SqlAlchemyCompanyRepository against in-memory SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.companies.company import Company
from app.infrastructure.database.repositories.sqlalchemy_company_repository import (
    SqlAlchemyCompanyRepository,
)


def _make_company(created_by: UUID, **overrides) -> Company:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        legal_name="Repo Test SAS",
        address="10 rue du Test, 75000 Paris",
        siret="12345678901234",
        tva_number="FR12345678901",
        iban="FR76300",
        bic="BNPAFRPP",
        logo_url=None,
        default_payment_terms=None,
        prefix_override=None,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Company(**defaults)


@pytest.fixture
def repo(session):
    return SqlAlchemyCompanyRepository(session)


@pytest.fixture
def creator_id():
    return uuid4()


class TestCompanyRepositoryCRUD:
    def test_save_and_find_by_id(self, repo, creator_id):
        company = _make_company(created_by=creator_id)
        saved = repo.save(company)
        found = repo.find_by_id(saved.id)
        assert found is not None
        assert found.id == saved.id
        assert found.legal_name == "Repo Test SAS"

    def test_find_by_id_returns_none_for_missing(self, repo):
        assert repo.find_by_id(uuid4()) is None

    def test_update_via_save(self, repo, creator_id):
        company = _make_company(created_by=creator_id)
        repo.save(company)
        updated = company.with_updates(legal_name="Updated SAS")
        repo.save(updated)
        found = repo.find_by_id(company.id)
        assert found.legal_name == "Updated SAS"

    def test_delete_removes_company(self, repo, creator_id):
        company = _make_company(created_by=creator_id)
        repo.save(company)
        repo.delete(company.id)
        assert repo.find_by_id(company.id) is None

    def test_list_all_returns_all(self, repo, creator_id):
        for i in range(3):
            repo.save(_make_company(created_by=creator_id, legal_name=f"Company {i}"))
        companies, total = repo.list_all(limit=50, offset=0)
        assert total == 3
        assert len(companies) == 3

    def test_list_all_pagination(self, repo, creator_id):
        for i in range(5):
            repo.save(_make_company(created_by=creator_id, legal_name=f"Company {i}"))
        page1, total = repo.list_all(limit=2, offset=0)
        page2, _ = repo.list_all(limit=2, offset=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        ids1 = {c.id for c in page1}
        ids2 = {c.id for c in page2}
        assert ids1.isdisjoint(ids2)

    def test_optional_fields_persisted_as_none(self, repo, creator_id):
        company = _make_company(
            created_by=creator_id,
            siret=None, tva_number=None, iban=None, bic=None,
        )
        repo.save(company)
        found = repo.find_by_id(company.id)
        assert found.siret is None
        assert found.tva_number is None
