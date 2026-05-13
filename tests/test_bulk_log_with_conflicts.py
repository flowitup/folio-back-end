"""Tests for cross-project conflict handling in BulkLogAttendanceUseCase
(Phase 4 — cook 4b).

Verifies the use-case-level contract:
  - conflicts present without acknowledge_conflicts → raises
    ConflictsNotAcknowledgedError carrying the conflict payload
  - same call with acknowledge_conflicts=True → proceeds normally
  - no conflicts → use case behaves identically regardless of the flag
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.labor import (
    BulkLogAttendanceEntry,
    BulkLogAttendanceRequest,
    BulkLogAttendanceUseCase,
    ConflictsNotAcknowledgedError,
)
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
        email="bulk_conflict@test.com",
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
        legal_name="Acme",
        address="addr",
        created_by=owner.id,
    )
    session.add(c)
    session.commit()
    return c


def _proj(session, owner, company, name):
    p = ProjectModel(
        id=uuid4(),
        name=name,
        address=name,
        owner_id=owner.id,
        company_id=company.id,
    )
    session.add(p)
    session.commit()
    return p


def _person(session, owner, name):
    p = PersonModel(
        id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        created_by_user_id=owner.id,
    )
    session.add(p)
    session.commit()
    return p


def _worker(repo, project, person):
    return repo.create(
        Worker(
            id=uuid4(),
            project_id=project.id,
            name=person.name,
            daily_rate=Decimal("100"),
            phone=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            person_id=person.id,
        )
    )


def _log_other(repo, worker_id, d):
    return repo.create(
        LaborEntry(
            id=uuid4(),
            worker_id=worker_id,
            date=d,
            amount_override=None,
            note=None,
            shift_type="full",
            supplement_hours=0,
            created_at=datetime.now(timezone.utc),
        )
    )


class TestBulkLogWithConflicts:
    def test_raises_when_conflicts_unacknowledged(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)
        usecase = BulkLogAttendanceUseCase(worker_repo, entry_repo, session)

        a = _proj(session, owner, company, "A")
        b = _proj(session, owner, company, "B")
        hugo = _person(session, owner, "Hugo")
        w_a = _worker(worker_repo, a, hugo)
        w_b = _worker(worker_repo, b, hugo)
        _log_other(entry_repo, w_b.id, date(2026, 5, 13))

        with pytest.raises(ConflictsNotAcknowledgedError) as ei:
            usecase.execute(
                BulkLogAttendanceRequest(
                    project_id=a.id,
                    date=date(2026, 5, 13),
                    entries=[
                        BulkLogAttendanceEntry(
                            worker_id=w_a.id, shift_type="full"
                        )
                    ],
                )
            )
        assert len(ei.value.conflicts) == 1
        assert ei.value.conflicts[0].person_id == hugo.id

    def test_proceeds_with_acknowledge_flag(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)
        usecase = BulkLogAttendanceUseCase(worker_repo, entry_repo, session)

        a = _proj(session, owner, company, "A")
        b = _proj(session, owner, company, "B")
        hugo = _person(session, owner, "Hugo")
        w_a = _worker(worker_repo, a, hugo)
        w_b = _worker(worker_repo, b, hugo)
        _log_other(entry_repo, w_b.id, date(2026, 5, 13))

        result = usecase.execute(
            BulkLogAttendanceRequest(
                project_id=a.id,
                date=date(2026, 5, 13),
                entries=[
                    BulkLogAttendanceEntry(worker_id=w_a.id, shift_type="half")
                ],
                acknowledge_conflicts=True,
            )
        )
        assert len(result.created) == 1
        assert result.skipped_worker_ids == []

    def test_no_conflicts_path_unchanged(self, session, owner, company):
        worker_repo = SQLAlchemyWorkerRepository(session)
        entry_repo = SQLAlchemyLaborEntryRepository(session)
        usecase = BulkLogAttendanceUseCase(worker_repo, entry_repo, session)

        a = _proj(session, owner, company, "Solo")
        hugo = _person(session, owner, "Hugo")
        w_a = _worker(worker_repo, a, hugo)

        result = usecase.execute(
            BulkLogAttendanceRequest(
                project_id=a.id,
                date=date(2026, 5, 13),
                entries=[
                    BulkLogAttendanceEntry(worker_id=w_a.id, shift_type="full")
                ],
            )
        )
        assert len(result.created) == 1
