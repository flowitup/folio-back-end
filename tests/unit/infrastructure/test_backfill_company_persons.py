"""company_persons backfill SQL (shared with migration 7d3e9a1b4c5f)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.infrastructure.database.backfills.company_persons_from_workers import run_backfill
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.worker import WorkerModel

NOW = datetime.now(timezone.utc)


def _company(session, created_by) -> CompanyModel:
    c = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue",
        created_by=created_by,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(c)
    return c


def _person(session, user, name: str) -> PersonModel:
    p = PersonModel(
        id=uuid4(), name=name, normalized_name=name.lower(), created_by_user_id=user.id, created_at=NOW, updated_at=NOW
    )
    session.add(p)
    return p


def _setup(session):
    user = UserModel(id=uuid4(), email=f"u-{uuid4().hex[:6]}@t.com", password_hash="x" * 60, is_active=True)
    session.add(user)
    session.flush()
    return user


def test_profiles_derived_from_workers_then_sole_company(session):
    user = _setup(session)
    company = _company(session, user.id)
    session.flush()
    project = ProjectModel(id=uuid4(), name="P", owner_id=user.id, company_id=company.id)
    session.add(project)
    worker_person = _person(session, user, "Worker")
    lonely_person = _person(session, user, "Lonely")
    session.flush()
    session.add(
        WorkerModel(
            id=uuid4(),
            project_id=project.id,
            person_id=worker_person.id,
            name="Worker",
            daily_rate=Decimal("150"),
            is_active=True,
        )
    )
    session.flush()

    from_workers, from_sole, missing = run_backfill(session.connection())

    assert (from_workers, from_sole, missing) == (1, 1, 0)
    rows = {r.person_id: r for r in session.query(CompanyPersonModel).filter_by(company_id=company.id).all()}
    assert rows[worker_person.id].default_daily_rate == Decimal("150")
    assert rows[lonely_person.id].default_daily_rate is None
    assert all(r.is_active for r in rows.values())


def test_no_sole_company_leaves_unlinked_persons_alone_and_is_idempotent(session):
    user = _setup(session)
    _company(session, user.id)
    _company(session, user.id)
    _person(session, user, "Nobody")
    session.flush()

    assert run_backfill(session.connection()) == (0, 0, 1)
    assert run_backfill(session.connection()) == (0, 0, 1)
