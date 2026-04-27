"""Integration tests for SqlAlchemyNoteRepository.list_due_for_user — real DB.

fire_at SQL math test:
    Insert a note with due_date=today and lead_time=0.
    fire_at = combine(today, 09:00) UTC.
    Query at 08:59 UTC → 0 results (not yet due).
    Query at 09:01 UTC → 1 result (just past fire_at).

Requires TEST_DATABASE_URL to point to a real Postgres instance.
Skipped automatically when TEST_DATABASE_URL is not set (SQLite in-memory).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

UTC = timezone.utc

# Skip the entire module when not running against Postgres.
pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="Requires Postgres TEST_DATABASE_URL for fire_at SQL math tests",
)


@pytest.fixture(scope="module")
def pg_app():
    """Flask app wired against the Postgres TEST_DATABASE_URL."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.adapters.jwt_issuer import JWTTokenIssuer
    from app.infrastructure.adapters.flask_session import FlaskSessionManager
    from app.infrastructure.adapters.sqlalchemy_user import SQLAlchemyUserRepository
    from app.infrastructure.adapters.sqlalchemy_project import SQLAlchemyProjectRepository
    from app.infrastructure.database.repositories.sqlalchemy_invitation import (
        SqlAlchemyInvitationRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.infrastructure.database.repositories.sqlalchemy_role import SqlAlchemyRoleRepository
    from config import TestingConfig
    from wiring import configure_container

    class PgTestConfig(TestingConfig):
        DATABASE_URL = os.environ["TEST_DATABASE_URL"]
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    app = create_app(PgTestConfig)
    with app.app_context():
        db.create_all()
        configure_container(
            user_repository=SQLAlchemyUserRepository(db.session),
            project_repository=SQLAlchemyProjectRepository(db.session),
            password_hasher=Argon2PasswordHasher(),
            token_issuer=JWTTokenIssuer(),
            session_manager=FlaskSessionManager(),
            invitation_repo=SqlAlchemyInvitationRepository(db.session),
            project_membership_repo=SqlAlchemyProjectMembershipRepository(db.session),
            role_repo=SqlAlchemyRoleRepository(db.session),
        )
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def pg_session(pg_app):
    """Per-test transactional rollback session for Postgres integration tests."""
    from app import db

    with pg_app.app_context():
        connection = db.engine.connect()
        transaction = connection.begin()
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=connection)
        session = Session()
        yield session
        session.close()
        transaction.rollback()
        connection.close()


def _insert_user_and_project(session):
    """Seed one user + project + membership, return (user_id, project_id)."""
    from sqlalchemy import text

    user_id = uuid4()
    project_id = uuid4()
    now = datetime.now(UTC)

    session.execute(
        text(
            "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
            "VALUES (:id, :email, :pw, true, :now, :now)"
        ),
        {"id": str(user_id), "email": f"{user_id}@test.local", "pw": "hash", "now": now},
    )
    session.execute(
        text(
            "INSERT INTO projects (id, name, owner_id, created_at, updated_at) "
            "VALUES (:id, :name, :owner, :now, :now)"
        ),
        {"id": str(project_id), "name": "Test project", "owner": str(user_id), "now": now},
    )
    # Add user as project member (no role required — user_projects role_id is nullable in tests)
    session.execute(
        text(
            "INSERT INTO user_projects (user_id, project_id, assigned_at) "
            "VALUES (:uid, :pid, :now) ON CONFLICT DO NOTHING"
        ),
        {"uid": str(user_id), "pid": str(project_id), "now": now},
    )
    session.flush()
    return user_id, project_id


def _insert_note(session, *, project_id, user_id, due_date, lead_time=0):
    """Insert a raw note row; return its UUID."""
    from sqlalchemy import text

    note_id = uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            "INSERT INTO notes "
            "(id, project_id, created_by, title, description, due_date, lead_time_minutes, "
            " status, created_at, updated_at) "
            "VALUES (:id, :pid, :uid, 'Test', NULL, :due, :lead, 'open', :now, :now)"
        ),
        {
            "id": str(note_id),
            "pid": str(project_id),
            "uid": str(user_id),
            "due": due_date,
            "lead": lead_time,
            "now": now,
        },
    )
    session.flush()
    return note_id


class TestFireAtSqlMath:
    """Verify the SQL fire_at formula matches Note.fire_at() Python computation."""

    def test_query_before_fire_at_returns_empty(self, pg_session):
        """At 08:59 UTC for lead_time=0, the note is not yet due → 0 results."""
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )
        from app.domain.entities.note import Note

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=0)

        # fire_at = 09:00 UTC; query at 08:59 → not due
        before_fire = datetime(today.year, today.month, today.day, 8, 59, 0, tzinfo=UTC)

        # Verify Python formula agrees
        python_fire_at = Note.fire_at(today, 0)
        assert python_fire_at == datetime(today.year, today.month, today.day, 9, 0, 0, tzinfo=UTC)
        assert before_fire < python_fire_at

        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=before_fire, limit=100)
        assert results == []

    def test_query_after_fire_at_returns_one_result(self, pg_session):
        """At 09:01 UTC for lead_time=0, the note is due → 1 result."""
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        note_id = _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=0)

        # fire_at = 09:00 UTC; query at 09:01 → due
        after_fire = datetime(today.year, today.month, today.day, 9, 1, 0, tzinfo=UTC)

        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=after_fire, limit=100)
        assert len(results) == 1
        assert results[0].id == note_id

    def test_fire_at_python_formula_matches_sql_boundary(self, pg_session):
        """fire_at boundary: query exactly AT fire_at timestamp → 1 result (<=)."""
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )
        from app.domain.entities.note import Note

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        note_id = _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=0)

        exact_fire_at = Note.fire_at(today, 0)  # 09:00:00 UTC

        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=exact_fire_at, limit=100)
        assert len(results) == 1
        assert results[0].id == note_id

    def test_lead_time_60_fire_at_is_08_00(self, pg_session):
        """lead_time=60 → fire_at=08:00 UTC; query at 07:59 → 0 results."""
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )
        from app.domain.entities.note import Note

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=60)

        python_fire_at = Note.fire_at(today, 60)
        assert python_fire_at == datetime(today.year, today.month, today.day, 8, 0, 0, tzinfo=UTC)

        before = datetime(today.year, today.month, today.day, 7, 59, 0, tzinfo=UTC)
        repo = SqlAlchemyNoteRepository(pg_session)
        assert repo.list_due_for_user(user_id=user_id, now=before, limit=100) == []

        after = datetime(today.year, today.month, today.day, 8, 1, 0, tzinfo=UTC)
        results = repo.list_due_for_user(user_id=user_id, now=after, limit=100)
        assert len(results) == 1

    def test_dismissed_note_not_returned(self, pg_session):
        """A note the user dismissed must not appear in due results."""
        from sqlalchemy import text
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        note_id = _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=0)

        # Dismiss the note
        pg_session.execute(
            text("INSERT INTO notes_dismissed (user_id, note_id, dismissed_at) " "VALUES (:uid, :nid, :now)"),
            {"uid": str(user_id), "nid": str(note_id), "now": datetime.now(UTC)},
        )
        pg_session.flush()

        after_fire = datetime(today.year, today.month, today.day, 9, 1, 0, tzinfo=UTC)
        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=after_fire, limit=100)
        assert results == []

    def test_done_note_not_returned(self, pg_session):
        """A 'done' note must not appear in due results."""
        from sqlalchemy import text
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        note_id = _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today, lead_time=0)
        pg_session.execute(
            text("UPDATE notes SET status = 'done' WHERE id = :id"),
            {"id": str(note_id)},
        )
        pg_session.flush()

        after_fire = datetime(today.year, today.month, today.day, 9, 1, 0, tzinfo=UTC)
        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=after_fire, limit=100)
        assert results == []

    def test_limit_cap_respected(self, pg_session):
        """Query with limit=2 returns at most 2 results even when more exist."""
        from app.infrastructure.database.repositories.sqlalchemy_note_repository import (
            SqlAlchemyNoteRepository,
        )

        user_id, project_id = _insert_user_and_project(pg_session)
        today = date.today()
        for _ in range(5):
            _insert_note(pg_session, project_id=project_id, user_id=user_id, due_date=today)

        after_fire = datetime(today.year, today.month, today.day, 9, 1, 0, tzinfo=UTC)
        repo = SqlAlchemyNoteRepository(pg_session)
        results = repo.list_due_for_user(user_id=user_id, now=after_fire, limit=2)
        assert len(results) <= 2
