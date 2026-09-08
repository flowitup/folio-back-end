"""scripts/qa_personas.py — create the three personas, then remove every trace.

Runs against in-memory SQLite through the app's own models, so the purge walks
the real foreign-key graph.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

SUFFIX = "unit"
PASSWORD = "Passw0rd!"
EMAILS = {
    "admin": "qa.admin@example.com",
    "manager": "qa.manager@example.com",
    "member": "qa.member@example.com",
}


@pytest.fixture(scope="module")
def qa_app():
    from app import create_app, db
    from config import TestingConfig

    class QaPersonaConfig(TestingConfig):
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    app = create_app(QaPersonaConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _count(sql: str, **params) -> int:
    from app import db

    return db.session.execute(text(sql), params).scalar() or 0


def _create(app):
    from scripts.qa_personas import create_personas

    with app.app_context():
        return create_personas(SUFFIX, EMAILS, PASSWORD, "1 rue de la Recette")


def _delete(app):
    from scripts.qa_personas import _company_name
    from scripts.qa_personas_purge import purge_company

    with app.app_context():
        return purge_company(_company_name(SUFFIX), list(EMAILS.values()))


def test_create_then_delete_leaves_zero_rows(qa_app):
    ids = _create(qa_app)
    assert set(ids) == {"company_id", "project_id", "admin_user_id", "manager_user_id", "member_user_id"}

    with qa_app.app_context():
        assert _count("SELECT COUNT(*) FROM companies WHERE legal_name = :n", n="Folio QA unit") == 1
        assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
        assert (
            _count(
                "SELECT COUNT(*) FROM user_company_access WHERE CAST(company_id AS TEXT) = :c",
                c=str(ids["company_id"]).replace("-", ""),
            )
            == 3
        )
        # Everyone is assigned to the project, and everyone has a directory profile.
        assert (
            _count(
                "SELECT COUNT(*) FROM user_projects WHERE CAST(project_id AS TEXT) = :p",
                p=str(ids["project_id"]).replace("-", ""),
            )
            == 3
        )
        assert _count("SELECT COUNT(*) FROM company_persons") == 3
        assert _count("SELECT COUNT(*) FROM persons") == 3

    counts = _delete(qa_app)
    assert counts["companies"] == 1
    assert counts["users"] == 3
    assert counts["projects"] == 1

    with qa_app.app_context():
        for table in (
            "companies",
            "users",
            "projects",
            "user_projects",
            "user_company_access",
            "company_persons",
            "persons",
        ):
            assert _count(f"SELECT COUNT(*) FROM {table}") == 0, f"{table} still has rows"


def test_create_is_idempotent(qa_app):
    first = _create(qa_app)
    second = _create(qa_app)
    assert first == second

    with qa_app.app_context():
        assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
        assert _count("SELECT COUNT(*) FROM projects") == 1

    _delete(qa_app)


def test_delete_refuses_a_company_outside_the_qa_namespace(qa_app):
    from scripts.qa_personas_purge import purge_company

    _create(qa_app)
    try:
        with qa_app.app_context():
            with pytest.raises(ValueError, match="not a 'Folio QA' company"):
                purge_company("ANN ECO", list(EMAILS.values()))
    finally:
        _delete(qa_app)


def test_delete_refuses_a_user_attached_elsewhere(qa_app):
    from datetime import datetime, timezone
    from uuid import uuid4

    from app import db
    from app.infrastructure.database.models import CompanyModel, UserModel
    from scripts.qa_personas import _company_name
    from scripts.qa_personas_purge import purge_company

    _create(qa_app)
    try:
        with qa_app.app_context():
            now = datetime.now(timezone.utc)
            outsider = UserModel(email="real.user@example.com", password_hash="x" * 60, is_active=True)
            db.session.add(outsider)
            db.session.flush()
            db.session.add(
                CompanyModel(
                    id=uuid4(),
                    legal_name="Real Co",
                    address="1 rue",
                    created_by=outsider.id,
                    created_at=now,
                    updated_at=now,
                )
            )
            db.session.commit()

            with pytest.raises(ValueError, match="not attached"):
                purge_company(_company_name(SUFFIX), [*EMAILS.values(), "real.user@example.com"])

            # Refused before any DELETE ran.
            assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
    finally:
        _delete(qa_app)
        with qa_app.app_context():
            db.session.execute(text("DELETE FROM companies"))
            db.session.execute(text("DELETE FROM users"))
            db.session.commit()
