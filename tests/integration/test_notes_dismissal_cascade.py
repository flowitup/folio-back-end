"""Integration tests for FK cascade behaviour — real Postgres DB required.

Cascade invariants:
  1. DELETE project  → notes rows deleted via FK ON DELETE CASCADE
  2. DELETE note     → notes_dismissed rows deleted via FK ON DELETE CASCADE

Skipped automatically when TEST_DATABASE_URL is not set / not Postgres.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

UTC = timezone.utc

pytestmark = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="Requires Postgres TEST_DATABASE_URL for cascade FK tests",
)


# ---------------------------------------------------------------------------
# Fixtures — reuse the pg_app/pg_session from the due-query module via import
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pg_app():
    """Flask app wired against the Postgres TEST_DATABASE_URL (cascade tests)."""
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

    class PgCascadeTestConfig(TestingConfig):
        DATABASE_URL = os.environ["TEST_DATABASE_URL"]
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    app = create_app(PgCascadeTestConfig)
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
    """Per-test transactional rollback session."""
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_user(session) -> uuid4:
    from sqlalchemy import text

    uid = uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
            "VALUES (:id, :email, 'hash', true, :now, :now)"
        ),
        {"id": str(uid), "email": f"{uid}@cascade.test", "now": now},
    )
    session.flush()
    return uid


def _seed_project(session, owner_id) -> uuid4:
    from sqlalchemy import text

    pid = uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            "INSERT INTO projects (id, name, owner_id, created_at, updated_at) "
            "VALUES (:id, :name, :owner, :now, :now)"
        ),
        {"id": str(pid), "name": "Cascade test project", "owner": str(owner_id), "now": now},
    )
    session.flush()
    return pid


def _seed_note(session, *, project_id, created_by) -> uuid4:
    from sqlalchemy import text

    nid = uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            "INSERT INTO notes "
            "(id, project_id, created_by, title, description, due_date, lead_time_minutes, "
            " status, created_at, updated_at) "
            "VALUES (:id, :pid, :uid, 'Cascade test note', NULL, :due, 0, 'open', :now, :now)"
        ),
        {
            "id": str(nid),
            "pid": str(project_id),
            "uid": str(created_by),
            "due": date.today(),
            "now": now,
        },
    )
    session.flush()
    return nid


def _seed_dismissal(session, *, user_id, note_id) -> None:
    from sqlalchemy import text

    session.execute(
        text("INSERT INTO notes_dismissed (user_id, note_id, dismissed_at) " "VALUES (:uid, :nid, :now)"),
        {"uid": str(user_id), "nid": str(note_id), "now": datetime.now(UTC)},
    )
    session.flush()


def _count_notes(session, project_id) -> int:
    from sqlalchemy import text

    row = session.execute(
        text("SELECT COUNT(*) FROM notes WHERE project_id = :pid"),
        {"pid": str(project_id)},
    ).fetchone()
    return row[0]


def _count_dismissals(session, note_id) -> int:
    from sqlalchemy import text

    row = session.execute(
        text("SELECT COUNT(*) FROM notes_dismissed WHERE note_id = :nid"),
        {"nid": str(note_id)},
    ).fetchone()
    return row[0]


def _delete_project(session, project_id) -> None:
    from sqlalchemy import text

    session.execute(
        text("DELETE FROM projects WHERE id = :id"),
        {"id": str(project_id)},
    )
    session.flush()


def _delete_note(session, note_id) -> None:
    from sqlalchemy import text

    session.execute(
        text("DELETE FROM notes WHERE id = :id"),
        {"id": str(note_id)},
    )
    session.flush()


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestProjectDeleteCascadesToNotes:
    def test_delete_project_removes_notes(self, pg_session):
        """Deleting a project must cascade-delete all its notes."""
        user_id = _seed_user(pg_session)
        project_id = _seed_project(pg_session, user_id)
        _seed_note(pg_session, project_id=project_id, created_by=user_id)
        _seed_note(pg_session, project_id=project_id, created_by=user_id)

        assert _count_notes(pg_session, project_id) == 2

        _delete_project(pg_session, project_id)

        assert _count_notes(pg_session, project_id) == 0

    def test_delete_project_with_dismissals_cascades_fully(self, pg_session):
        """Cascade chain: project → notes → notes_dismissed, all rows removed."""
        user_id = _seed_user(pg_session)
        project_id = _seed_project(pg_session, user_id)
        note_id = _seed_note(pg_session, project_id=project_id, created_by=user_id)
        _seed_dismissal(pg_session, user_id=user_id, note_id=note_id)

        assert _count_dismissals(pg_session, note_id) == 1

        _delete_project(pg_session, project_id)

        # Both notes and dismissals gone
        assert _count_notes(pg_session, project_id) == 0
        assert _count_dismissals(pg_session, note_id) == 0


class TestNoteDeleteCascadesToDismissals:
    def test_delete_note_removes_dismissals(self, pg_session):
        """Deleting a note must cascade-delete all its dismissal records."""
        user_id = _seed_user(pg_session)
        project_id = _seed_project(pg_session, user_id)
        note_id = _seed_note(pg_session, project_id=project_id, created_by=user_id)

        # Two members dismiss the same note
        user2 = _seed_user(pg_session)
        _seed_dismissal(pg_session, user_id=user_id, note_id=note_id)
        _seed_dismissal(pg_session, user_id=user2, note_id=note_id)

        assert _count_dismissals(pg_session, note_id) == 2

        _delete_note(pg_session, note_id)

        assert _count_dismissals(pg_session, note_id) == 0

    def test_delete_note_leaves_other_notes_intact(self, pg_session):
        """Cascade only affects the deleted note, not siblings in the same project."""
        user_id = _seed_user(pg_session)
        project_id = _seed_project(pg_session, user_id)
        note_a = _seed_note(pg_session, project_id=project_id, created_by=user_id)
        note_b = _seed_note(pg_session, project_id=project_id, created_by=user_id)
        _seed_dismissal(pg_session, user_id=user_id, note_id=note_a)
        _seed_dismissal(pg_session, user_id=user_id, note_id=note_b)

        _delete_note(pg_session, note_a)

        # note_b's dismissal must be untouched
        assert _count_dismissals(pg_session, note_b) == 1
