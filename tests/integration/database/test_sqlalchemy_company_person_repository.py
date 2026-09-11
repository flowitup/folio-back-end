"""Integration tests for SqlAlchemyCompanyPersonRepository (Phase 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.company_person import CompanyPerson
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.repositories.sqlalchemy_company_person_repository import (
    SqlAlchemyCompanyPersonRepository,
)

PASSWORD_HASH = "x" * 60


def _make_user(session, email: str) -> UserModel:
    u = UserModel(id=uuid4(), email=email, is_active=True)
    session.add(u)
    return u


def _make_company(session, created_by) -> CompanyModel:
    now = datetime.now(timezone.utc)
    c = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue X",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(c)
    return c


def _make_person(session, name: str, created_by) -> PersonModel:
    p = PersonModel(
        id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        created_by_user_id=created_by,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(p)
    return p


@pytest.fixture
def repo(session):
    return SqlAlchemyCompanyPersonRepository(session)


@pytest.fixture
def world(session):
    owner = _make_user(session, "cp-owner@test.com")
    session.flush()
    company_a = _make_company(session, owner.id)
    company_b = _make_company(session, owner.id)
    person = _make_person(session, "Jean Dupont", owner.id)
    session.flush()
    session.commit()
    return {"owner": owner, "company_a": company_a, "company_b": company_b, "person": person}


def test_save_and_find(repo, world):
    profile = CompanyPerson(
        id=uuid4(),
        company_id=world["company_a"].id,
        person_id=world["person"].id,
        created_at=datetime.now(timezone.utc),
        default_daily_rate=Decimal("150.00"),
        phone_normalized="+33612345678",
    )
    repo.save(profile)

    found = repo.find(world["company_a"].id, world["person"].id)
    assert found is not None
    assert found.default_daily_rate == Decimal("150.00")
    assert found.phone_normalized == "+33612345678"
    assert found.is_active is True


def test_find_scoped_to_company(repo, world):
    profile = CompanyPerson(
        id=uuid4(),
        company_id=world["company_a"].id,
        person_id=world["person"].id,
        created_at=datetime.now(timezone.utc),
    )
    repo.save(profile)

    assert repo.find(world["company_b"].id, world["person"].id) is None


def test_find_by_phone_scoped_to_company(repo, world):
    repo.save(
        CompanyPerson(
            id=uuid4(),
            company_id=world["company_a"].id,
            person_id=world["person"].id,
            created_at=datetime.now(timezone.utc),
            phone_normalized="+33612345678",
        )
    )
    found = repo.find_by_phone(world["company_a"].id, "+33612345678")
    assert found is not None
    assert found.person_id == world["person"].id
    assert repo.find_by_phone(world["company_b"].id, "+33612345678") is None


def test_list_for_company_excludes_inactive_by_default(repo, session, world):
    active = CompanyPerson(
        id=uuid4(),
        company_id=world["company_a"].id,
        person_id=world["person"].id,
        created_at=datetime.now(timezone.utc),
        is_active=True,
    )
    repo.save(active)
    other_person = _make_person(session, "Marie Curie", world["owner"].id)
    session.commit()
    inactive = CompanyPerson(
        id=uuid4(),
        company_id=world["company_a"].id,
        person_id=other_person.id,
        created_at=datetime.now(timezone.utc),
        is_active=False,
    )
    repo.save(inactive)

    active_only = repo.list_for_company(world["company_a"].id)
    assert [p.person_id for p in active_only] == [world["person"].id]

    with_inactive = repo.list_for_company(world["company_a"].id, include_inactive=True)
    assert len(with_inactive) == 2


def test_deactivate(repo, world):
    repo.save(
        CompanyPerson(
            id=uuid4(),
            company_id=world["company_a"].id,
            person_id=world["person"].id,
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
    )
    assert repo.deactivate(world["company_a"].id, world["person"].id) is True
    found = repo.find(world["company_a"].id, world["person"].id)
    assert found.is_active is False


def test_deactivate_missing_returns_false(repo, world):
    assert repo.deactivate(world["company_a"].id, uuid4()) is False


def test_list_pending_by_phone_excludes_expired(repo, world):
    now = datetime.now(timezone.utc)
    repo.save(
        CompanyPerson(
            id=uuid4(),
            company_id=world["company_a"].id,
            person_id=world["person"].id,
            created_at=now,
            phone_normalized="+33699999999",
            pending_expires_at=now + timedelta(days=1),
        )
    )
    still_pending = repo.list_pending_by_phone("+33699999999", now)
    assert len(still_pending) == 1

    expired_check = repo.list_pending_by_phone("+33699999999", now + timedelta(days=2))
    assert expired_check == []


def test_list_for_person_across_companies(repo, session, world):
    repo.save(
        CompanyPerson(
            id=uuid4(),
            company_id=world["company_a"].id,
            person_id=world["person"].id,
            created_at=datetime.now(timezone.utc),
        )
    )
    repo.save(
        CompanyPerson(
            id=uuid4(),
            company_id=world["company_b"].id,
            person_id=world["person"].id,
            created_at=datetime.now(timezone.utc),
        )
    )
    profiles = repo.list_for_person(world["person"].id)
    assert {p.company_id for p in profiles} == {world["company_a"].id, world["company_b"].id}
