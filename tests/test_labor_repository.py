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
        )
        session.add(entry1)
        session.commit()

        entry2 = LaborEntryModel(
            id=uuid4(),
            worker_id=worker.id,
            date=date.today(),  # Same date
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
            created_at=datetime.now(timezone.utc),
        )
        entry_repo.create(entry1)

        entry2 = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker_entity.id,
            date=date.today(),  # Same date
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
                created_at=datetime.now(timezone.utc),
            )
            entry_repo.create(entry)

        result = entry_repo.get_summary(sample_project.id)

        assert len(result) == 1
        assert result[0].worker_name == "Worker A"
        assert result[0].days_worked == 3
        assert result[0].total_cost == Decimal("300.00")
