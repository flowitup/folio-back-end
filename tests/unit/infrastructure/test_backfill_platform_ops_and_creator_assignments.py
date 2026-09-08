"""Relationship backfills: creator assignments + directory profiles.

The legacy-role → ops/company-role mapping these used to sit next to now lives
inside migration 9a4c1e7b2d05 and is covered by the Postgres migration tests —
its tables no longer exist in the models, so SQLite cannot host it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from app.domain.authz.resolver import has_permission
from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import run_backfill
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader

NOW = datetime.now(timezone.utc)


def _user(session, email: str) -> UserModel:
    user = UserModel(id=uuid4(), email=email, password_hash="x" * 60, is_active=True)
    session.add(user)
    return user


def _company(session, created_by) -> CompanyModel:
    company = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue",
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(company)
    return company


def _attach(session, user, company, role: str, is_primary: bool = True) -> None:
    session.add(
        UserCompanyAccessModel(
            user_id=user.id, company_id=company.id, role=role, is_primary=is_primary, attached_at=NOW
        )
    )


def test_creator_is_assigned_and_can_read_their_project_afterwards(session):
    owner = _user(session, "owner@test.com")
    session.flush()
    company = _company(session, owner.id)
    session.flush()
    project = ProjectModel(id=uuid4(), name="P", owner_id=owner.id, company_id=company.id)
    session.add(project)
    session.flush()

    # Before: the owner has no company role and no assignment → no permission.
    reader = SqlAlchemyAuthzReader(session)
    assert has_permission(reader, owner.id, "project:read", project_id=project.id) is False

    report = run_backfill(session.connection())
    session.expire_all()  # the backfill writes in SQL; drop the ORM identity map

    assert report.creator_assignments == 1
    assert report.owner_access_created == 1
    rows = session.execute(
        text("SELECT COUNT(*) FROM user_projects WHERE CAST(project_id AS TEXT) = :pid"),
        {"pid": str(project.id).replace("-", "")},
    ).scalar()
    assert rows == 1
    assert has_permission(reader, owner.id, "project:read", project_id=project.id) is True
    assert has_permission(reader, owner.id, "project:update", project_id=project.id) is True
    # Manager, not admin: deleting a project stays admin-only (D2).
    assert has_permission(reader, owner.id, "project:delete", project_id=project.id) is False


def test_every_attachment_gets_a_directory_profile(session):
    user = _user(session, "attached@test.com")
    session.flush()
    company = _company(session, user.id)
    session.flush()
    _attach(session, user, company, "member")
    session.flush()

    report = run_backfill(session.connection())
    session.expire_all()  # the backfill writes in SQL; drop the ORM identity map

    assert (report.persons_created, report.profiles_created) == (1, 1)
    profile = session.query(CompanyPersonModel).filter_by(company_id=company.id).one()
    assert profile.is_active is True
    person = session.execute(
        text("SELECT name, user_id FROM persons WHERE CAST(id AS TEXT) = :pid"),
        {"pid": str(profile.person_id).replace("-", "")},
    ).fetchone()
    assert person[0] == "attached@test.com"


def test_backfill_is_idempotent(session):
    ops = _user(session, "idempotent@test.com")
    session.flush()
    company = _company(session, ops.id)
    session.flush()
    _attach(session, ops, company, "member")
    project = ProjectModel(id=uuid4(), name="P", owner_id=ops.id, company_id=company.id)
    session.add(project)
    session.flush()

    first = run_backfill(session.connection())
    second = run_backfill(session.connection())
    session.expire_all()

    assert first.creator_assignments == 1
    assert second.creator_assignments == 0
    assert second.profiles_created == 0


def test_profile_keeps_the_phone_the_person_already_carries(session):
    """L5: a pre-existing identity's phone must reach the new directory profile."""
    from app.infrastructure.database.models.person import PersonModel

    user = _user(session, "hasperson@test.com")
    session.flush()
    company = _company(session, user.id)
    session.flush()
    _attach(session, user, company, "member")
    session.add(
        PersonModel(
            id=uuid4(),
            name="Has Person",
            normalized_name="has person",
            phone="+33611111111",
            phone_normalized="+33611111111",
            user_id=user.id,
            created_by_user_id=user.id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()

    report = run_backfill(session.connection())
    session.expire_all()

    assert report.persons_created == 0
    profile = session.query(CompanyPersonModel).filter_by(company_id=company.id).one()
    assert profile.phone_normalized == "+33611111111"


def test_a_phone_already_used_in_the_company_yields_a_profile_without_it(session):
    """H2/H4: Postgres has a partial UNIQUE(company_id, phone_normalized).

    SQLite cannot reproduce the index, so the collision is simulated by
    pre-inserting the row that would occupy the phone: the backfill must create
    the missing profile anyway, without the phone, instead of writing a
    duplicate the deploy would abort on.
    """
    from app.infrastructure.database.models.person import PersonModel

    phone = "+33622222222"
    user = _user(session, "collides@test.com")
    session.flush()
    company = _company(session, user.id)
    session.flush()
    _attach(session, user, company, "member")

    # The user's own identity carries the phone…
    session.add(
        PersonModel(
            id=uuid4(),
            name="Collides",
            normalized_name="collides",
            phone=phone,
            phone_normalized=phone,
            user_id=user.id,
            created_by_user_id=user.id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    # …and an admin already added a pending profile for the same number.
    pending_person = PersonModel(
        id=uuid4(),
        name="Pending Worker",
        normalized_name="pending worker",
        phone=phone,
        phone_normalized=phone,
        created_by_user_id=user.id,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(pending_person)
    session.flush()
    session.add(
        CompanyPersonModel(
            id=uuid4(),
            company_id=company.id,
            person_id=pending_person.id,
            is_active=True,
            phone_normalized=phone,
            created_by_user_id=user.id,
            created_at=NOW,
        )
    )
    session.flush()

    report = run_backfill(session.connection())
    session.expire_all()

    assert report.profiles_created == 1
    assert report.profiles_without_phone == 1
    assert any("without their phone" in w for w in report.warnings)
    with_phone = session.query(CompanyPersonModel).filter_by(company_id=company.id, phone_normalized=phone).all()
    assert len(with_phone) == 1, "the phone must stay unique inside the company"
    profiles = session.query(CompanyPersonModel).filter_by(company_id=company.id).all()
    assert len(profiles) == 2
    linked = [p for p in profiles if p.person_id != pending_person.id]
    assert linked[0].phone_normalized is None


def test_projects_without_a_company_are_counted_and_warned_about(session):
    """H3: no abort (migrations run at container start) — a WARNING line instead."""
    owner = _user(session, "orphanowner@test.com")
    session.flush()
    session.add(ProjectModel(id=uuid4(), name="No company", owner_id=owner.id, company_id=None))
    session.flush()

    report = run_backfill(session.connection())

    assert report.projects_without_company == 1
    assert any(w.startswith("WARNING:") and "company_id IS NULL" in w for w in report.warnings)
