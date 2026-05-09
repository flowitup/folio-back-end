"""Tests for Worker and LaborEntry models and repositories."""

import pytest
from decimal import Decimal
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import (
    WorkerModel,
    LaborEntryModel,
    ProjectModel,
    UserModel,
)
from app.infrastructure.adapters.sqlalchemy_worker import SQLAlchemyWorkerRepository
from app.infrastructure.adapters.sqlalchemy_labor_entry import SQLAlchemyLaborEntryRepository
from app.domain.entities.worker import Worker
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import DuplicateEntryError


@pytest.fixture
def owner_user(session):
    """Create owner user for projects."""
    user = UserModel(id=uuid4(), email="labor_owner@test.com", password_hash="hashed", is_active=True)
    session.add(user)
    session.commit()
    return user


@pytest.fixture
def sample_project(session, owner_user):
    """Create a sample project for labor tests."""
    project = ProjectModel(id=uuid4(), name="Labor Test Project", address="123 Labor St", owner_id=owner_user.id)
    session.add(project)
    session.commit()
    return project


@pytest.fixture
def worker_repo(session):
    """Create worker repository with test session."""
    return SQLAlchemyWorkerRepository(session)


@pytest.fixture
def entry_repo(session):
    """Create labor entry repository with test session."""
    return SQLAlchemyLaborEntryRepository(session)


@pytest.fixture
def sample_worker_entity(sample_project):
    """Create a sample worker domain entity."""
    return Worker(
        id=uuid4(),
        project_id=sample_project.id,
        name="Test Worker",
        daily_rate=Decimal("100.00"),
        phone="+33612345678",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


class TestWorkerModel:
    """Test WorkerModel CRUD operations."""

    def test_create_worker(self, session, sample_project):
        """Test creating a new worker."""
        worker = WorkerModel(
            id=uuid4(),
            project_id=sample_project.id,
            name="New Worker",
            daily_rate=Decimal("100.00"),
            phone="+33612345678",
        )
        session.add(worker)
        session.commit()

        result = session.get(WorkerModel, worker.id)
        assert result is not None
        assert result.name == "New Worker"
        assert result.daily_rate == Decimal("100.00")
        assert result.is_active is True

    def test_worker_soft_delete(self, session, sample_project):
        """Test soft delete sets is_active to False."""
        worker = WorkerModel(
            id=uuid4(),
            project_id=sample_project.id,
            name="To Delete",
            daily_rate=Decimal("100.00"),
        )
        session.add(worker)
        session.commit()

        worker.is_active = False
        session.commit()

        result = session.get(WorkerModel, worker.id)
        assert result.is_active is False


class TestLaborEntryModel:
    """Test LaborEntryModel CRUD operations."""

    def test_create_labor_entry(self, session, sample_project):
        """Test creating a new labor entry."""
        worker = WorkerModel(
            id=uuid4(),
            project_id=sample_project.id,
            name="Worker",
            daily_rate=Decimal("100.00"),
        )
        session.add(worker)
        session.commit()

        entry = LaborEntryModel(
            id=uuid4(),
            worker_id=worker.id,
            date=date.today(),
            amount_override=Decimal("120.00"),
            note="Overtime",
            shift_type="full",
        )
        session.add(entry)
        session.commit()

        result = session.get(LaborEntryModel, entry.id)
        assert result is not None
        assert result.worker_id == worker.id
        assert result.amount_override == Decimal("120.00")

    def test_duplicate_entry_raises_error(self, session, sample_project):
        """Test that duplicate (worker_id, date) raises IntegrityError."""
        worker = WorkerModel(
            id=uuid4(),
            project_id=sample_project.id,
            name="Worker",
            daily_rate=Decimal("100.00"),
        )
        session.add(worker)
        session.commit()

        entry1 = LaborEntryModel(
            id=uuid4(),
            worker_id=worker.id,
            date=date.today(),
            shift_type="full",
        )
        session.add(entry1)
        session.commit()

        entry2 = LaborEntryModel(
            id=uuid4(),
            worker_id=worker.id,
            date=date.today(),  # Same date
            shift_type="full",
        )
        session.add(entry2)

        with pytest.raises(IntegrityError):
            session.commit()


class TestSQLAlchemyWorkerRepository:
    """Test SQLAlchemyWorkerRepository operations."""

    def test_create_worker(self, worker_repo, sample_worker_entity):
        """Test repository create method."""
        result = worker_repo.create(sample_worker_entity)

        assert result.id == sample_worker_entity.id
        assert result.name == sample_worker_entity.name

    def test_find_by_id(self, worker_repo, sample_worker_entity):
        """Test finding worker by ID."""
        worker_repo.create(sample_worker_entity)

        result = worker_repo.find_by_id(sample_worker_entity.id)

        assert result is not None
        assert result.id == sample_worker_entity.id

    def test_find_by_id_not_found(self, worker_repo):
        """Test finding non-existent worker returns None."""
        result = worker_repo.find_by_id(uuid4())
        assert result is None

    def test_list_by_project_active_only(self, worker_repo, sample_project):
        """Test listing only active workers."""
        active_worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Active",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        inactive_worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Inactive",
            daily_rate=Decimal("100.00"),
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )

        worker_repo.create(active_worker)
        worker_repo.create(inactive_worker)

        result = worker_repo.list_by_project(sample_project.id, active_only=True)

        assert len(result) == 1
        assert result[0].name == "Active"

    def test_soft_delete(self, worker_repo, sample_worker_entity):
        """Test soft delete sets is_active to False."""
        worker_repo.create(sample_worker_entity)

        success = worker_repo.soft_delete(sample_worker_entity.id)

        assert success is True
        result = worker_repo.find_by_id(sample_worker_entity.id)
        assert result.is_active is False


class TestSQLAlchemyLaborEntryRepository:
    """Test SQLAlchemyLaborEntryRepository operations."""

    def test_create_entry(self, entry_repo, worker_repo, sample_worker_entity):
        """Test creating labor entry."""
        worker_repo.create(sample_worker_entity)

        entry = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker_entity.id,
            date=date.today(),
            shift_type="full",
            created_at=datetime.now(timezone.utc),
        )

        result = entry_repo.create(entry)

        assert result.id == entry.id
        assert result.worker_id == sample_worker_entity.id

    def test_create_duplicate_raises_error(self, entry_repo, worker_repo, sample_worker_entity):
        """Test creating duplicate entry raises DuplicateEntryError."""
        worker_repo.create(sample_worker_entity)

        entry1 = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker_entity.id,
            date=date.today(),
            shift_type="full",
            created_at=datetime.now(timezone.utc),
        )
        entry_repo.create(entry1)

        entry2 = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker_entity.id,
            date=date.today(),  # Same date
            shift_type="full",
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(DuplicateEntryError):
            entry_repo.create(entry2)

    def test_get_summary(self, entry_repo, worker_repo, sample_project):
        """Test summary aggregation."""
        worker1 = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Worker A",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker1)

        # Create 3 entries for worker1
        for i in range(3):
            entry = LaborEntry(
                id=uuid4(),
                worker_id=worker1.id,
                date=date(2026, 1, i + 1),
                shift_type="full",
                created_at=datetime.now(timezone.utc),
            )
            entry_repo.create(entry)

        result = entry_repo.get_summary(sample_project.id)

        assert len(result) == 1
        assert result[0].worker_name == "Worker A"
        assert result[0].days_worked == 3
        assert result[0].total_cost == Decimal("300.00")

    def test_insert_null_shift_with_supplement_ok(self, entry_repo, worker_repo, sample_project):
        """shift_type=NULL + supplement_hours=3 inserts and round-trips through _to_entity."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Null Shift Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        supplement_entry = LaborEntry(
            id=uuid4(),
            worker_id=worker.id,
            date=date(2026, 5, 1),
            shift_type=None,
            supplement_hours=3,
            created_at=datetime.now(timezone.utc),
        )
        result = entry_repo.create(supplement_entry)

        assert result.shift_type is None
        assert result.supplement_hours == 3

    def test_null_shift_unique_constraint_per_day(self, entry_repo, worker_repo, sample_project):
        """Unique (worker_id, date) constraint still holds for NULL-shift entries."""
        from app.domain.entities.worker import Worker
        from app.domain.exceptions.labor_exceptions import DuplicateEntryError

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Dup Null Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        entry1 = LaborEntry(
            id=uuid4(),
            worker_id=worker.id,
            date=date(2026, 5, 2),
            shift_type=None,
            supplement_hours=3,
            created_at=datetime.now(timezone.utc),
        )
        entry_repo.create(entry1)

        entry2 = LaborEntry(
            id=uuid4(),
            worker_id=worker.id,
            date=date(2026, 5, 2),  # same date
            shift_type=None,
            supplement_hours=2,
            created_at=datetime.now(timezone.utc),
        )
        with pytest.raises(DuplicateEntryError):
            entry_repo.create(entry2)

    def test_get_summary_includes_banked_hours(self, entry_repo, worker_repo, sample_project):
        """get_summary() correctly sums supplement_hours into banked_hours."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Banked Sum Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        # 3 entries: 1 priced full + 2 supplement-only
        entries_data = [
            (date(2026, 6, 1), "full", 0),  # priced day, 0 supplement
            (date(2026, 6, 2), None, 4),  # supplement-only, 4h
            (date(2026, 6, 3), None, 5),  # supplement-only, 5h
        ]
        for d, shift, sup in entries_data:
            e = LaborEntry(
                id=uuid4(),
                worker_id=worker.id,
                date=d,
                shift_type=shift,
                supplement_hours=sup,
                created_at=datetime.now(timezone.utc),
            )
            entry_repo.create(e)

        results = entry_repo.get_summary(
            sample_project.id,
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 30),
        )

        assert len(results) == 1
        row = results[0]
        assert row.banked_hours == 9  # 0 + 4 + 5
        # Standalone supplement-only rows (shift_type=None) are not counted as worked days;
        # they are off-day extra hours per Q5.
        assert row.days_worked == 1
        # priced cost: 1 full day * 100 = 100; supplement-only days → 0
        assert row.total_cost == Decimal("100.00")

    def test_update_entry_patch_supplement_preserves_other_fields(self, entry_repo, worker_repo, sample_project):
        """PATCH supplement_hours only — all other fields survive the round-trip."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Preserve Fields Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        original = LaborEntry(
            id=uuid4(),
            worker_id=worker.id,
            date=date(2026, 7, 1),
            shift_type="half",
            supplement_hours=0,
            note="original note",
            created_at=datetime.now(timezone.utc),
        )
        entry_repo.create(original)

        # Simulate PATCH: only supplement_hours changes
        patched = LaborEntry(
            id=original.id,
            worker_id=original.worker_id,
            date=original.date,
            shift_type=original.shift_type,
            supplement_hours=6,
            note=original.note,
            created_at=original.created_at,
        )
        result = entry_repo.update(patched)

        assert result.supplement_hours == 6
        assert result.shift_type == "half"
        assert result.note == "original note"

    def test_delete_persists_after_commit(self, entry_repo, worker_repo, sample_worker_entity, session):
        """Regression: delete() must commit so the row is gone after request end.

        Mirrors PR #29 on SQLAlchemyProjectRepository — bulk query.delete()
        without session.commit() left the unit-of-work uncommitted, so the
        DELETE endpoint returned 204 in production but the row survived.
        """
        worker_repo.create(sample_worker_entity)
        entry = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker_entity.id,
            date=date(2026, 8, 1),
            shift_type="full",
            created_at=datetime.now(timezone.utc),
        )
        entry_repo.create(entry)

        deleted = entry_repo.delete(entry.id)

        assert deleted is True
        # Bypass the SQLAlchemy identity map to confirm the row is gone in DB.
        session.expire_all()
        assert session.get(LaborEntryModel, entry.id) is None

    def test_delete_returns_false_when_missing(self, entry_repo):
        """Repository.delete() returns False when no row matches."""
        assert entry_repo.delete(uuid4()) is False

    def test_list_by_project_respects_limit(self, entry_repo, worker_repo, sample_project):
        """list_by_project(limit=N) returns at most N rows, most recent first."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Limit Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        # Insert 5 entries on 5 distinct dates
        for day in range(1, 6):
            entry_repo.create(
                LaborEntry(
                    id=uuid4(),
                    worker_id=worker.id,
                    date=date(2026, 9, day),
                    shift_type="full",
                    created_at=datetime.now(timezone.utc),
                )
            )

        capped = entry_repo.list_by_project(sample_project.id, limit=2)

        assert len(capped) == 2
        # Order is date desc — most recent two are Sep 5 and Sep 4
        assert capped[0].date == date(2026, 9, 5)
        assert capped[1].date == date(2026, 9, 4)

    def test_get_monthly_summary_groups_by_year_and_month(self, entry_repo, worker_repo, sample_project):
        """get_monthly_summary returns one row per (year, month) ordered DESC."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Monthly Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        # 3 entries in Mar 2026, 2 entries in Apr 2026, 1 entry in May 2025.
        # Plus 1 supplement-only row in Apr 2026 — should NOT count toward
        # total_days or total_cost (matches per-worker summary behavior).
        entries_data = [
            (date(2025, 5, 10), "full", 0),
            (date(2026, 3, 1), "full", 0),
            (date(2026, 3, 2), "full", 0),
            (date(2026, 3, 3), "half", 0),
            (date(2026, 4, 1), "full", 0),
            (date(2026, 4, 2), "full", 0),
            (date(2026, 4, 5), None, 4),  # supplement-only row
        ]
        for d, shift, sup in entries_data:
            entry_repo.create(
                LaborEntry(
                    id=uuid4(),
                    worker_id=worker.id,
                    date=d,
                    shift_type=shift,
                    supplement_hours=sup,
                    created_at=datetime.now(timezone.utc),
                )
            )

        rows = entry_repo.get_monthly_summary(sample_project.id)

        # Three buckets, ordered most-recent first.
        assert len(rows) == 3
        assert (rows[0].year, rows[0].month) == (2026, 4)
        assert (rows[1].year, rows[1].month) == (2026, 3)
        assert (rows[2].year, rows[2].month) == (2025, 5)

        # April: 2 priced (full + full), supplement-only row is excluded.
        assert rows[0].total_days == 2
        assert rows[0].total_cost == Decimal("200.00")  # 2 × 100 (full)

        # March: 2 full + 1 half = 2.5 priced days. days_worked counts
        # priced rows (=3); cost is 2×100 + 0.5×100 = 250.
        assert rows[1].total_days == 3
        assert rows[1].total_cost == Decimal("250.00")

        # May 2025: single full day.
        assert rows[2].total_days == 1
        assert rows[2].total_cost == Decimal("100.00")

    def test_get_monthly_summary_returns_empty_when_no_entries(self, entry_repo, sample_project):
        """get_monthly_summary on a project without entries returns []."""
        assert entry_repo.get_monthly_summary(sample_project.id) == []

    def test_list_by_project_no_limit_returns_all(self, entry_repo, worker_repo, sample_project):
        """list_by_project() without limit returns every matching row."""
        from app.domain.entities.worker import Worker

        worker = Worker(
            id=uuid4(),
            project_id=sample_project.id,
            name="Unbounded Worker",
            daily_rate=Decimal("100.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        worker_repo.create(worker)

        for day in range(1, 4):
            entry_repo.create(
                LaborEntry(
                    id=uuid4(),
                    worker_id=worker.id,
                    date=date(2026, 10, day),
                    shift_type="full",
                    created_at=datetime.now(timezone.utc),
                )
            )

        all_rows = entry_repo.list_by_project(sample_project.id)
        assert len(all_rows) == 3
