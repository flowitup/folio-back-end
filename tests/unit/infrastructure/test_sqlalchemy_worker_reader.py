"""Repository-level tests for SQLAlchemyWorkerReader.

Runs against SQLite in-memory DB using the shared session fixture from conftest.
Covers get_for_project: same-project match, COALESCE(persons.name, workers.name)
display-name resolution, cross-project rejection, and unknown worker id.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.infrastructure.adapters.sqlalchemy_worker_reader import SQLAlchemyWorkerReader
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.worker import WorkerModel


def _now():
    return datetime.now(timezone.utc)


def _make_user(session) -> UUID:
    user = UserModel(
        id=uuid4(),
        email=f"u{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(user)
    session.flush()
    return user.id


def _make_project(session, owner_id: UUID) -> UUID:
    project = ProjectModel(id=uuid4(), name=f"P-{uuid4().hex[:6]}", owner_id=owner_id)
    session.add(project)
    session.flush()
    return project.id


def _make_person(session, owner_id: UUID, name: str) -> UUID:
    person = PersonModel(
        id=uuid4(),
        name=name,
        normalized_name=name.lower().strip(),
        created_by_user_id=owner_id,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(person)
    session.flush()
    return person.id


def _make_worker(session, project_id: UUID, name: str, person_id: "UUID | None" = None) -> UUID:
    worker = WorkerModel(
        id=uuid4(),
        project_id=project_id,
        person_id=person_id,
        name=name,
        daily_rate=Decimal("100.00"),
        is_active=True,
    )
    session.add(worker)
    session.flush()
    return worker.id


class TestGetForProject:
    def test_returns_worker_name_when_no_person_linked(self, session):
        owner_id = _make_user(session)
        project_id = _make_project(session, owner_id)
        worker_id = _make_worker(session, project_id, "Worker Only Name")
        reader = SQLAlchemyWorkerReader(session)

        result = reader.get_for_project(worker_id, project_id)

        assert result is not None
        assert result.id == worker_id
        assert result.project_id == project_id
        assert result.display_name == "Worker Only Name"

    def test_prefers_person_name_over_worker_name(self, session):
        """COALESCE(persons.name, workers.name) — person's name wins when linked."""
        owner_id = _make_user(session)
        project_id = _make_project(session, owner_id)
        person_id = _make_person(session, owner_id, "Real Person Name")
        worker_id = _make_worker(session, project_id, "Stale Worker Name", person_id=person_id)
        reader = SQLAlchemyWorkerReader(session)

        result = reader.get_for_project(worker_id, project_id)

        assert result is not None
        assert result.display_name == "Real Person Name"

    def test_returns_none_for_cross_project_worker(self, session):
        """A worker that exists but belongs to a different project must return None."""
        owner_id = _make_user(session)
        project_id = _make_project(session, owner_id)
        other_project_id = _make_project(session, owner_id)
        worker_id = _make_worker(session, other_project_id, "Cross Project Worker")
        reader = SQLAlchemyWorkerReader(session)

        result = reader.get_for_project(worker_id, project_id)

        assert result is None

    def test_returns_none_for_unknown_worker_id(self, session):
        owner_id = _make_user(session)
        project_id = _make_project(session, owner_id)
        reader = SQLAlchemyWorkerReader(session)

        result = reader.get_for_project(uuid4(), project_id)

        assert result is None
