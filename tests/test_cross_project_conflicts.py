"""Tests for the cross-project conflict query (Phase 4 — cook 4a).

Verifies:
  - empty result when only a single project exists
  - single-conflict detection across two projects in the same company
  - inactive Worker rows are ignored
  - the company-scope filter prevents cross-company leakage
  - person_ids filter narrows the result
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.labor_entry import LaborEntry
from app.domain.entities.worker import Worker
from app.infrastructure.adapters.sqlalchemy_labor_entry import (
    SQLAlchemyLaborEntryRepository,
)
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
from app.infrastructure.database.models import (
    CompanyModel,
    PersonModel,
    ProjectModel,
    UserModel,
)


@pytest.fixture
def owner(session):
    u = UserModel(
        id=uuid4(),
        email="conflict_owner@test.com",
        password_hash="x",
        is_active=True,
    )
    session.add(u)
    session.commit()
    return u


@pytest.fixture
def company(session, owner):
    c = CompanyModel(
        id=uuid4(),
        legal_name="Acme Construction",
        address="1 Test St",
        created_by=owner.id,
    )
    session.add(c)
    session.commit()
    return c


@pytest.fixture
def other_company(session, owner):
    c = CompanyModel(
        id=uuid4(),
        legal_name="Other Co",
        address="2 Other St",
        created_by=owner.id,
    )
    session.add(c)
    session.commit()
    return c


def _make_project(session, owner, company, name):
    p = ProjectModel(
        id=uuid4(),
        name=name,
        address=f"{name} addr",
        owner_id=owner.id,
        company_id=company.id,
    )
    session.add(p)
    session.commit()
    return p


def _make_person(session, owner, name):
    p = PersonModel(
        id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        created_by_user_id=owner.id,
    )
    session.add(p)
    session.commit()
    return p


def _make_worker(repo, project, person, active=True):
    w = Worker(
        id=uuid4(),
        project_id=project.id,
        name=person.name,
        daily_rate=Decimal("100.00"),
        phone=None,
        is_active=active,
        created_at=datetime.now(timezone.utc),
        person_id=person.id,
    )
    return repo.create(w)


def _log(repo, worker_id, d, shift_type="full"):
    return repo.create(
        LaborEntry(
            id=uuid4(),
            worker_id=worker_id,
            date=d,
            amount_override=None,
            note=None,
            shift_type=shift_type,
            supplement_hours=0,
            created_at=datetime.now(timezone.utc),
        )
    )


class TestCrossProjectConflicts:
    def test_empty_when_only_one_project(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)

        project_a = _make_project(session, owner, company, "Alpha")
        hugo = _make_person(session, owner, "Hugo")
        _make_worker(worker_repo, project_a, hugo)

        result = entry_repo.find_cross_project_conflicts(
            project_id=project_a.id, date=date(2026, 5, 13)
        )
        assert result == []

    def test_detects_single_cross_project_conflict(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)

        project_a = _make_project(session, owner, company, "Alpha")
        project_b = _make_project(session, owner, company, "Beta")
        hugo = _make_person(session, owner, "Hugo Martin")
        _make_worker(worker_repo, project_a, hugo)
        worker_b = _make_worker(worker_repo, project_b, hugo)
        _log(entry_repo, worker_b.id, date(2026, 5, 13), shift_type="half")

        result = entry_repo.find_cross_project_conflicts(
            project_id=project_a.id, date=date(2026, 5, 13)
        )
        assert len(result) == 1
        assert result[0].person_id == hugo.id
        assert result[0].person_name == "Hugo Martin"
        assert len(result[0].entries) == 1
        assert result[0].entries[0].project_name == "Beta"
        assert result[0].entries[0].shift_type == "half"

    def test_ignores_inactive_other_worker(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)

        project_a = _make_project(session, owner, company, "A")
        project_b = _make_project(session, owner, company, "B")
        leo = _make_person(session, owner, "Léo")
        _make_worker(worker_repo, project_a, leo)
        worker_b = _make_worker(worker_repo, project_b, leo, active=False)
        _log(entry_repo, worker_b.id, date(2026, 5, 13))

        result = entry_repo.find_cross_project_conflicts(
            project_id=project_a.id, date=date(2026, 5, 13)
        )
        assert result == []

    def test_isolates_across_companies(
        self, session, owner, company, other_company
    ):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)

        project_a = _make_project(session, owner, company, "Alpha")
        project_other = _make_project(session, owner, other_company, "Other")
        hugo = _make_person(session, owner, "Hugo")
        _make_worker(worker_repo, project_a, hugo)
        worker_other = _make_worker(worker_repo, project_other, hugo)
        _log(entry_repo, worker_other.id, date(2026, 5, 13))

        # No conflict — the other project lives in a different company.
        result = entry_repo.find_cross_project_conflicts(
            project_id=project_a.id, date=date(2026, 5, 13)
        )
        assert result == []

    def test_filters_by_person_ids(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)

        project_a = _make_project(session, owner, company, "A")
        project_b = _make_project(session, owner, company, "B")
        hugo = _make_person(session, owner, "Hugo")
        leo = _make_person(session, owner, "Léo")
        for person in (hugo, leo):
            _make_worker(worker_repo, project_a, person)
            wb = _make_worker(worker_repo, project_b, person)
            _log(entry_repo, wb.id, date(2026, 5, 13))

        result = entry_repo.find_cross_project_conflicts(
            project_id=project_a.id,
            date=date(2026, 5, 13),
            person_ids=[hugo.id],
        )
        assert len(result) == 1
        assert result[0].person_id == hugo.id
