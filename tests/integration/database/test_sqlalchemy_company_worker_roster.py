"""Company worker roster: workers follow the company across its projects (Postgres only)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.labor.enroll_company_workers import EnrollCompanyWorkersUseCase
from app.infrastructure.adapters.sqlalchemy_company_worker_roster import SqlAlchemyCompanyWorkerRoster
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.labor_role import LaborRoleModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.worker import WorkerModel


_TABLES = ("users", "companies", "projects", "persons", "labor_roles", "workers")


@pytest.fixture
def session(engine):
    """Only the tables the roster touches: the shared create_all does not build on PostgreSQL."""
    if engine.dialect.name != "postgresql":
        pytest.skip("roster copy uses PostgreSQL (DISTINCT ON, gen_random_uuid)")
    from sqlalchemy.orm import sessionmaker

    from app.infrastructure.database.models import Base

    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.create_all(engine, tables=tables)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        Base.metadata.drop_all(engine, tables=tables)


def _company(session, owner):
    now = datetime.now(timezone.utc)
    c = CompanyModel(
        id=uuid4(), legal_name=f"Co {uuid4().hex[:6]}", address="1 rue X", created_by=owner.id,
        created_at=now, updated_at=now,
    )
    session.add(c)
    session.flush()
    return c


def _project(session, owner, company):
    p = ProjectModel(id=uuid4(), name=f"P {uuid4().hex[:6]}", owner_id=owner.id, company_id=company.id)
    session.add(p)
    session.flush()
    return p


def _person(session, owner, name):
    now = datetime.now(timezone.utc)
    p = PersonModel(
        id=uuid4(), name=name, normalized_name=name.lower(), created_by_user_id=owner.id,
        created_at=now, updated_at=now,
    )
    session.add(p)
    session.flush()
    return p


def _worker(session, project, person, rate="100", role=None, active=True):
    w = WorkerModel(
        id=uuid4(), project_id=project.id, person_id=person.id, name=person.name,
        daily_rate=Decimal(rate), role_id=role.id if role else None, is_active=active,
    )
    session.add(w)
    session.flush()
    return w


@pytest.fixture
def world(session):
    owner = UserModel(id=uuid4(), email=f"{uuid4().hex[:8]}@x.fr", is_active=True)
    session.add(owner)
    session.flush()
    company = _company(session, owner)
    other_company = _company(session, owner)
    projects = [_project(session, owner, company) for _ in range(3)]
    foreign = _project(session, owner, other_company)
    return owner, company, other_company, projects, foreign


def _rows(session, project):
    return session.query(WorkerModel).filter(WorkerModel.project_id == project.id).all()


def test_worker_added_on_one_site_appears_on_the_others(session, world):
    owner, company, _, (a, b, c), foreign = world
    role = LaborRoleModel(id=uuid4(), company_id=company.id, name="Thợ chính", color="#000000")
    session.add(role)
    session.flush()
    tien = _person(session, owner, "Tiến")
    _worker(session, a, tien, rate="120", role=role)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.after_worker_created(a.id, tien.id) == 2

    for project in (b, c):
        (row,) = _rows(session, project)
        assert (row.person_id, row.daily_rate, row.role_id, row.is_active) == (tien.id, Decimal("120"), role.id, True)
    assert _rows(session, foreign) == []


def test_new_project_starts_with_every_active_worker(session, world):
    owner, company, _, (a, b, c), _ = world
    active, gone = _person(session, owner, "Ân"), _person(session, owner, "Việt")
    _worker(session, a, active)
    _worker(session, a, gone, active=False)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.after_project_created(b.id) == 1
    assert [r.person_id for r in _rows(session, b)] == [active.id]


def test_sync_is_idempotent_and_keeps_existing_rows(session, world):
    owner, company, _, (a, b, c), _ = world
    dung = _person(session, owner, "Dũng")
    _worker(session, a, dung, rate="100")
    custom = _worker(session, b, dung, rate="150", active=False)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.sync_company(company.id) == 1  # only c lacked him
    assert usecase.sync_company(company.id) == 0
    session.refresh(custom)
    assert (custom.daily_rate, custom.is_active) == (Decimal("150"), False)


def test_role_of_another_company_is_not_copied(session, world):
    owner, company, other_company, (a, b, _), _ = world
    foreign_role = LaborRoleModel(id=uuid4(), company_id=other_company.id, name="X", color="#000000")
    session.add(foreign_role)
    session.flush()
    long = _person(session, owner, "Long")
    _worker(session, a, long, role=foreign_role)

    EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session)).sync_company(company.id)
    assert _rows(session, b)[0].role_id is None


def test_failure_never_breaks_the_caller(session, world):
    class Boom:
        def enroll_person(self, *_):
            raise RuntimeError("db down")

        enroll_company_workers_in_project = enroll_person

    usecase = EnrollCompanyWorkersUseCase(Boom())
    assert usecase.after_worker_created(uuid4(), uuid4()) == 0
    assert usecase.after_project_created(uuid4()) == 0
