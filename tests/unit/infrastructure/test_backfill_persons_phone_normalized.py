"""Phase 2 — persons.user_id / persons.phone_normalized backfill (migration 2ca24be9e3a8).

See app.infrastructure.database.backfills.persons_phone_normalized.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.database.backfills.persons_phone_normalized import (
    backfill_phone_normalized,
    backfill_user_id_from_workers,
    run_backfill,
)
from app.infrastructure.database.models import PersonModel, ProjectModel, UserModel, WorkerModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from tests.company_tenancy_helper import company_for_projects

PASSWORD_HASH = "x" * 60


def _make_user(session, email: str) -> UserModel:
    u = UserModel(id=uuid4(), email=email, is_active=True)
    session.add(u)
    return u


def _make_person(session, name: str, phone, created_by) -> PersonModel:
    p = PersonModel(
        id=uuid4(),
        name=name,
        phone=phone,
        normalized_name=name.lower(),
        created_by_user_id=created_by,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(p)
    return p


def test_backfill_user_id_links_single_person(session):
    owner = _make_user(session, "backfill-owner@test.com")
    linked_user = _make_user(session, "backfill-linked@test.com")
    session.flush()
    person = _make_person(session, "Jean Dupont", "+33612345678", owner.id)
    session.flush()
    project = ProjectModel(
        id=uuid4(), name="Backfill Project", owner_id=owner.id, company_id=company_for_projects(session, owner.id)
    )
    session.add(project)
    session.flush()
    worker = WorkerModel(
        id=uuid4(),
        project_id=project.id,
        name="Jean Dupont",
        phone="+33612345678",
        daily_rate=100,
        is_active=True,
        person_id=person.id,
        user_id=linked_user.id,
    )
    session.add(worker)
    session.commit()

    ambiguous = backfill_user_id_from_workers(session.connection())
    session.commit()
    session.refresh(person)

    assert ambiguous == []
    assert person.user_id == linked_user.id


def test_backfill_user_id_skips_ambiguous_user(session):
    owner = _make_user(session, "backfill-owner2@test.com")
    linked_user = _make_user(session, "backfill-linked2@test.com")
    session.flush()
    person_a = _make_person(session, "Person A", None, owner.id)
    person_b = _make_person(session, "Person B", None, owner.id)
    session.flush()
    project = ProjectModel(
        id=uuid4(), name="Ambiguous Project", owner_id=owner.id, company_id=company_for_projects(session, owner.id)
    )
    session.add(project)
    session.flush()
    worker_a = WorkerModel(
        id=uuid4(),
        project_id=project.id,
        name="A",
        daily_rate=100,
        is_active=True,
        person_id=person_a.id,
        user_id=linked_user.id,
    )
    session.add(worker_a)
    session.flush()
    # Second worker for the SAME linked_user but a DIFFERENT person, on a
    # second project (workers.user_id is unique per project, not globally).
    project2 = ProjectModel(
        id=uuid4(), name="Ambiguous Project 2", owner_id=owner.id, company_id=company_for_projects(session, owner.id)
    )
    session.add(project2)
    session.flush()
    worker_b = WorkerModel(
        id=uuid4(),
        project_id=project2.id,
        name="B",
        daily_rate=100,
        is_active=True,
        person_id=person_b.id,
        user_id=linked_user.id,
    )
    session.add(worker_b)
    session.commit()

    ambiguous = backfill_user_id_from_workers(session.connection())
    session.commit()
    session.refresh(person_a)
    session.refresh(person_b)

    assert ambiguous == [linked_user.id]
    assert person_a.user_id is None
    assert person_b.user_id is None


def test_backfill_phone_normalized_uses_fr_default_with_no_company(session):
    owner = _make_user(session, "phone-owner@test.com")
    session.flush()
    person = _make_person(session, "Phone Person", "0612345678", owner.id)
    session.commit()

    unparseable = backfill_phone_normalized(session.connection())
    session.commit()
    session.refresh(person)

    assert unparseable == 0
    assert person.phone_normalized == "+33612345678"


def test_backfill_phone_normalized_uses_company_region(session):
    owner = _make_user(session, "phone-owner-vn@test.com")
    session.flush()
    company = CompanyModel(
        id=uuid4(),
        legal_name="VN Co",
        address="1 duong X",
        created_by=owner.id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        default_phone_region="VN",
    )
    session.add(company)
    session.flush()
    person = _make_person(session, "VN Person", "0912345678", owner.id)
    session.flush()
    session.add(
        CompanyPersonModel(
            id=uuid4(),
            company_id=company.id,
            person_id=person.id,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    unparseable = backfill_phone_normalized(session.connection())
    session.commit()
    session.refresh(person)

    assert unparseable == 0
    assert person.phone_normalized == "+84912345678"


def test_backfill_phone_normalized_leaves_unparseable_null(session):
    owner = _make_user(session, "phone-owner-bad@test.com")
    session.flush()
    person = _make_person(session, "Bad Phone Person", "not-a-phone", owner.id)
    session.commit()

    unparseable = backfill_phone_normalized(session.connection())
    session.commit()
    session.refresh(person)

    assert unparseable == 1
    assert person.phone_normalized is None


def test_run_backfill_returns_both_results(session):
    owner = _make_user(session, "phone-owner-both@test.com")
    session.flush()
    _make_person(session, "Nothing To Link", "0612345678", owner.id)
    session.commit()

    ambiguous, unparseable = run_backfill(session.connection())
    assert ambiguous == []
    assert unparseable == 0
