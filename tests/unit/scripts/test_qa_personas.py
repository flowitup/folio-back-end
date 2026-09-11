"""scripts/qa_personas.py — create the three personas, then remove every trace.

Runs against in-memory SQLite through the app's own models, so the purge walks
the real foreign-key graph.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

SUFFIX = "unit"
EMAILS = {
    "admin": "qa.admin@example.com",
    "manager": "qa.manager@example.com",
    "member": "qa.member@example.com",
}
PHONES = {
    "admin": "+33600000101",
    "manager": "+33600000102",
    "member": "+33600000103",
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
        return create_personas(SUFFIX, EMAILS, PHONES, "1 rue de la Recette")


def _delete(app):
    from scripts.qa_personas import company_name
    from scripts.qa_personas_purge import purge_company

    with app.app_context():
        return purge_company(company_name(SUFFIX), list(EMAILS.values()))


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
    from scripts.qa_personas import company_name
    from scripts.qa_personas_purge import purge_company

    _create(qa_app)
    try:
        with qa_app.app_context():
            now = datetime.now(timezone.utc)
            outsider = UserModel(email="real.user@example.com", is_active=True)
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
                purge_company(company_name(SUFFIX), [*EMAILS.values(), "real.user@example.com"])

            # Refused before any DELETE ran.
            assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
    finally:
        _delete(qa_app)
        with qa_app.app_context():
            db.session.execute(text("DELETE FROM companies"))
            db.session.execute(text("DELETE FROM users"))
            db.session.commit()


def _seed_outside_world(app) -> dict:
    """A real company with a real user and a real project — none of it QA data."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app import db
    from app.infrastructure.database.models import CompanyModel, ProjectModel, UserModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    with app.app_context():
        now = datetime.now(timezone.utc)
        user = UserModel(id=uuid4(), email="real.user@example.com", is_active=True)
        db.session.add(user)
        company = CompanyModel(
            id=uuid4(), legal_name="Real Co", address="1 rue", created_by=user.id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.add(
            UserCompanyAccessModel(
                user_id=user.id, company_id=company.id, role="admin", is_primary=True, attached_at=now
            )
        )
        project = ProjectModel(id=uuid4(), name="Real Project", owner_id=user.id, company_id=company.id)
        db.session.add(project)
        db.session.commit()
        return {"user_id": user.id, "company_id": company.id, "project_id": project.id}


def _drop_outside_world(app) -> None:
    from app import db

    with app.app_context():
        db.session.execute(text("DELETE FROM projects"))
        db.session.execute(text("DELETE FROM user_company_access"))
        db.session.execute(text("DELETE FROM companies"))
        db.session.execute(text("DELETE FROM users"))
        db.session.commit()


def test_create_refuses_an_email_owned_by_someone_else(qa_app):
    """C1: a typo in --admin-email must not hand a real account to QA."""
    from app import db
    from app.infrastructure.database.models import UserModel
    from scripts.qa_personas import QaPersonaRefused, company_name, create_personas

    _seed_outside_world(qa_app)
    try:
        with qa_app.app_context():
            with pytest.raises(QaPersonaRefused, match="real.user@example.com"):
                create_personas(SUFFIX, {**EMAILS, "member": "real.user@example.com"}, PHONES, "1 rue")

            # Nothing was written, and the real account is untouched.
            real = db.session.query(UserModel).filter_by(email="real.user@example.com").one()
            assert real.phone is None
            assert real.is_active is True
            assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 0
            assert _count("SELECT COUNT(*) FROM companies WHERE legal_name = :n", n=company_name(SUFFIX)) == 0
    finally:
        _drop_outside_world(qa_app)


def test_delete_dry_run_counts_without_deleting(qa_app):
    from scripts.qa_personas import company_name as _name
    from scripts.qa_personas_purge import purge_company

    _create(qa_app)
    try:
        with qa_app.app_context():
            counts = purge_company(_name(SUFFIX), list(EMAILS.values()), dry_run=True)
            assert counts["users"] == 3
            assert counts["companies"] == 1
            assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
    finally:
        _delete(qa_app)


def test_delete_touches_nothing_outside_the_qa_scope(qa_app):
    """M1: a real company, its admin and its project survive the purge untouched."""
    outside = _seed_outside_world(qa_app)
    _create(qa_app)
    try:
        counts = _delete(qa_app)
        assert counts["users"] == 3  # the three QA personas, not the real one
        assert counts["companies"] == 1
        assert counts["projects"] == 1

        with qa_app.app_context():
            for table, column, value in (
                ("users", "id", outside["user_id"]),
                ("companies", "id", outside["company_id"]),
                ("projects", "id", outside["project_id"]),
            ):
                assert (
                    _count(
                        f"SELECT COUNT(*) FROM {table} WHERE REPLACE(LOWER(CAST({column} AS TEXT)), '-', '') = :v",
                        v=str(value).replace("-", "").lower(),
                    )
                    == 1
                ), f"{table} row was deleted outside the QA scope"
            assert _count("SELECT COUNT(*) FROM user_company_access") == 1
    finally:
        _drop_outside_world(qa_app)


def test_delete_refuses_a_qa_user_who_also_belongs_to_a_real_company(qa_app):
    """C1: only accounts whose sole attachment is the QA company may be purged."""
    from datetime import datetime, timezone

    from app import db
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
    from scripts.qa_personas import company_name as _name
    from scripts.qa_personas_purge import purge_company

    outside = _seed_outside_world(qa_app)
    ids = _create(qa_app)
    try:
        with qa_app.app_context():
            db.session.add(
                UserCompanyAccessModel(
                    user_id=ids["member_user_id"],
                    company_id=outside["company_id"],
                    role="member",
                    is_primary=False,
                    attached_at=datetime.now(timezone.utc),
                )
            )
            db.session.commit()

            with pytest.raises(ValueError, match="attached to 2 companies"):
                purge_company(_name(SUFFIX), list(EMAILS.values()))

            # Refused before any DELETE ran.
            assert _count("SELECT COUNT(*) FROM users WHERE email LIKE 'qa.%'") == 3
    finally:
        _drop_outside_world(qa_app)
