"""Company worker roster: workers follow the company across its projects (Postgres only)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.labor.enroll_company_workers import EnrollCompanyWorkersUseCase
from app.infrastructure.adapters.sqlalchemy_company_worker_roster import SqlAlchemyCompanyWorkerRoster
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.labor_role import LaborRoleModel
from app.infrastructure.database.models.person import PersonModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel
from app.infrastructure.database.models.worker import WorkerModel


_TABLES = (
    "users",
    "companies",
    "projects",
    "persons",
    "labor_roles",
    "company_persons",
    "workers",
    "worker_rate_changes",
    "user_company_access",
)


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
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue X",
        created_by=owner.id,
        created_at=now,
        updated_at=now,
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
        id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        created_by_user_id=owner.id,
        created_at=now,
        updated_at=now,
    )
    session.add(p)
    session.flush()
    return p


def _worker(session, project, person, rate="100", role=None, active=True):
    w = WorkerModel(
        id=uuid4(),
        project_id=project.id,
        person_id=person.id,
        name=person.name,
        daily_rate=Decimal(rate),
        role_id=role.id if role else None,
        is_active=active,
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


def _profile(session, company, person, rate=None, pending=False, active=True):
    cp = CompanyPersonModel(
        id=uuid4(),
        company_id=company.id,
        person_id=person.id,
        default_daily_rate=rate,
        is_active=active,
        pending_expires_at=datetime.now(timezone.utc) if pending else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(cp)
    session.flush()
    return cp


def test_stopping_a_worker_stops_them_on_every_project_of_the_company(session, world):
    owner, company, _, (a, b, c), foreign = world
    ngan = _person(session, owner, "Ngân")
    rows = [_worker(session, p, ngan) for p in (a, b, c)]
    elsewhere = _worker(session, foreign, ngan)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.after_worker_stopped(a.id, ngan.id) == 3
    for row in rows:
        session.refresh(row)
        assert row.is_active is False
    session.refresh(elsewhere)
    assert elsewhere.is_active is True

    assert usecase.after_worker_restarted(b.id, ngan.id) == 3
    session.refresh(rows[2])
    assert rows[2].is_active is True


def test_stopped_worker_is_not_brought_back_by_a_later_listing(session, world):
    owner, company, _, (a, b, c), _ = world
    hoa = _person(session, owner, "Hòa")
    for p in (a, b, c):
        _worker(session, p, hoa)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))
    usecase.after_worker_stopped(a.id, hoa.id)

    assert usecase.before_roster_listed(b.id) == 0
    assert all(r.is_active is False for p in (a, b, c) for r in _rows(session, p))


def test_directory_person_with_a_rate_gets_a_worker_on_each_project_when_it_is_listed(session, world):
    owner, company, other_company, (a, b, c), foreign = world
    role = LaborRoleModel(id=uuid4(), company_id=company.id, name="Thợ phụ", color="#000000")
    session.add(role)
    session.flush()
    thanh = _person(session, owner, "Thành")
    cp = _profile(session, company, thanh, rate=Decimal("90"))
    cp.labor_role_id = role.id
    session.flush()
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    for project in (a, b, c):
        assert usecase.before_roster_listed(project.id) == 1
    for project in (a, b, c):
        (row,) = _rows(session, project)
        assert (row.person_id, row.daily_rate, row.role_id) == (thanh.id, Decimal("90"), role.id)
    assert _rows(session, foreign) == []
    assert usecase.before_roster_listed(a.id) == 0


def test_directory_profiles_without_a_rate_or_still_pending_or_inactive_are_skipped(session, world):
    owner, company, _, (a, b, c), _ = world
    for name, kwargs in (
        ("Admin", {}),
        ("Zero", {"rate": Decimal("0")}),
        ("Invited", {"rate": Decimal("90"), "pending": True}),
        ("Left", {"rate": Decimal("90"), "active": False}),
    ):
        _profile(session, company, _person(session, owner, name), **kwargs)

    assert EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session)).before_roster_listed(a.id) == 0
    assert _rows(session, a) == []


def test_worker_row_wins_over_the_directory_profile(session, world):
    owner, company, _, (a, b, c), _ = world
    lan = _person(session, owner, "Lan")
    _profile(session, company, lan, rate=Decimal("90"))
    _worker(session, a, lan, rate="130")

    EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session)).before_roster_listed(b.id)
    assert [r.daily_rate for r in _rows(session, b)] == [Decimal("130")]


def test_copy_takes_the_rate_timeline_so_the_person_costs_the_same_everywhere(session, world):
    from datetime import date

    from app.infrastructure.database.models.worker_rate_change import WorkerRateChangeModel

    owner, company, _, (a, b, c), _ = world
    jean = _person(session, owner, "Jean")
    row_a = _worker(session, a, jean, rate="120.50")
    session.add(WorkerRateChangeModel(id=uuid4(), worker_id=row_a.id, effective_date=date(2026, 9, 15), daily_rate=130))
    session.flush()

    EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session)).after_project_created(c.id)

    (row_c,) = _rows(session, c)
    assert row_c.daily_rate == Decimal("120.50")
    changes = session.query(WorkerRateChangeModel).filter(WorkerRateChangeModel.worker_id == row_c.id).all()
    assert [(ch.effective_date, ch.daily_rate) for ch in changes] == [(date(2026, 9, 15), Decimal("130.00"))]


def test_person_stopped_everywhere_is_not_brought_back_by_their_directory_rate(session, world):
    owner, company, _, (a, b, c), _ = world
    lina = _person(session, owner, "Lina")
    _profile(session, company, lina, rate=Decimal("120"))
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))
    assert usecase.before_roster_listed(a.id) == 1
    usecase.after_worker_stopped(a.id, lina.id)
    (row_a,) = _rows(session, a)
    row_a.is_active = False
    session.flush()

    assert usecase.after_project_created(b.id) == 0
    assert usecase.before_roster_listed(c.id) == 0
    assert _rows(session, b) == [] and _rows(session, c) == []

    # Restarting brings them back, including on the sites created while they were stopped.
    usecase.after_worker_restarted(a.id, lina.id)
    assert len(_rows(session, b)) == 1 and len(_rows(session, c)) == 1


def test_stop_is_limited_to_the_sites_given(session, world):
    owner, company, _, (a, b, c), _ = world
    hoa = _person(session, owner, "Hương")
    rows = [_worker(session, p, hoa) for p in (a, b, c)]
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.after_worker_stopped(a.id, hoa.id, [a.id, b.id]) == 2
    for row in rows:
        session.refresh(row)
    assert [r.is_active for r in rows] == [False, False, True]


def test_restart_relinks_the_person_account_when_it_is_still_in_the_company(session, world):
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    owner, company, _, (a, b, c), _ = world
    account = UserModel(id=uuid4(), email=f"{uuid4().hex[:8]}@x.fr", is_active=True)
    session.add(account)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=account.id, company_id=company.id, is_primary=True, attached_at=datetime.now(timezone.utc)
        )
    )
    duc = _person(session, owner, "Đức")
    duc.user_id = account.id
    rows = [_worker(session, p, duc, active=False) for p in (a, b)]
    session.flush()

    EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session)).after_worker_restarted(a.id, duc.id)
    for row in rows:
        session.refresh(row)
        assert (row.is_active, row.user_id) == (True, account.id)


def test_role_change_follows_the_person_and_new_sites_take_it(session, world):
    owner, company, _, (a, b, c), _ = world
    role = LaborRoleModel(id=uuid4(), company_id=company.id, name="Ouvrier principal", color="#000000")
    session.add(role)
    session.flush()
    sau = _person(session, owner, "Sáu")
    profile = _profile(session, company, sau, rate=None)
    _worker(session, a, sau)
    _worker(session, b, sau)
    usecase = EnrollCompanyWorkersUseCase(SqlAlchemyCompanyWorkerRoster(session))

    assert usecase.after_role_changed(a.id, sau.id, role.id) == 2
    assert [r.role_id for p in (a, b) for r in _rows(session, p)] == [role.id, role.id]
    session.refresh(profile)
    assert profile.labor_role_id == role.id
    usecase.after_project_created(c.id)
    assert _rows(session, c)[0].role_id == role.id
