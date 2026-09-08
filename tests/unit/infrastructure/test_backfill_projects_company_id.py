"""H2 — projects.company_id backfill SQL (shared with migration 15c1df3fdbfa).

Runs the exact statements the migration executes against the SQLite test
session fixture — see app.infrastructure.database.backfills.projects_company_id.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.database.backfills.projects_company_id import (
    backfill_from_primary_company,
    backfill_from_sole_company,
    count_null_company_id,
    run_backfill,
)
from tests.company_tenancy_helper import relax_projects_company_id, restore_projects_company_id
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD_HASH = "x" * 60


@pytest.fixture(scope="module", autouse=True)
def projects_before_the_not_null(engine, tables):
    """These steps run while `projects.company_id` is still nullable."""
    relax_projects_company_id(engine)
    yield
    restore_projects_company_id(engine)


def _make_user(session, email: str) -> UserModel:
    u = UserModel(id=uuid4(), email=email, password_hash=PASSWORD_HASH, is_active=True)
    session.add(u)
    return u


def _make_company(session, created_by) -> CompanyModel:
    now = datetime.now(timezone.utc)
    c = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue X",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(c)
    return c


def test_backfill_from_primary_company_fills_orphaned_project(session):
    owner = _make_user(session, "owner-primary@test.com")
    session.flush()
    company = _make_company(session, owner.id)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=owner.id,
            company_id=company.id,
            role="admin",
            is_primary=True,
            attached_at=datetime.now(timezone.utc),
        )
    )
    project = ProjectModel(id=uuid4(), name="Orphan", owner_id=owner.id, company_id=None)
    session.add(project)
    session.commit()

    backfill_from_primary_company(session.connection())
    session.commit()
    session.refresh(project)

    assert project.company_id == company.id


def test_backfill_leaves_already_set_company_id_untouched(session):
    owner = _make_user(session, "owner-set@test.com")
    session.flush()
    company_a = _make_company(session, owner.id)
    company_b = _make_company(session, owner.id)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=owner.id,
            company_id=company_a.id,
            role="admin",
            is_primary=True,
            attached_at=datetime.now(timezone.utc),
        )
    )
    project = ProjectModel(id=uuid4(), name="Already Scoped", owner_id=owner.id, company_id=company_b.id)
    session.add(project)
    session.commit()

    backfill_from_primary_company(session.connection())
    session.commit()
    session.refresh(project)

    assert project.company_id == company_b.id


def test_backfill_from_sole_company_used_when_no_primary_flag(session):
    """Owner has exactly one company access row, but it isn't flagged primary."""
    owner = _make_user(session, "owner-sole@test.com")
    session.flush()
    company = _make_company(session, owner.id)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=owner.id,
            company_id=company.id,
            role="member",
            is_primary=False,
            attached_at=datetime.now(timezone.utc),
        )
    )
    project = ProjectModel(id=uuid4(), name="No Primary Flag", owner_id=owner.id, company_id=None)
    session.add(project)
    session.commit()

    backfill_from_primary_company(session.connection())
    session.commit()
    session.refresh(project)
    assert project.company_id is None  # pass 1 alone can't resolve it

    backfill_from_sole_company(session.connection())
    session.commit()
    session.refresh(project)
    assert project.company_id == company.id


def test_run_backfill_leaves_ambiguous_owner_null_and_reports_count(session):
    """Pass 2 (sole-company) must skip an owner with MULTIPLE companies —
    picking one would silently mis-scope the project."""
    ambiguous_owner = _make_user(session, "ambiguous@test.com")
    no_company_owner = _make_user(session, "no-company@test.com")
    session.flush()
    company_a = _make_company(session, ambiguous_owner.id)
    company_b = _make_company(session, ambiguous_owner.id)
    session.flush()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            UserCompanyAccessModel(
                user_id=ambiguous_owner.id, company_id=company_a.id, role="member", is_primary=False, attached_at=now
            ),
            UserCompanyAccessModel(
                user_id=ambiguous_owner.id, company_id=company_b.id, role="member", is_primary=False, attached_at=now
            ),
        ]
    )
    project_ambiguous = ProjectModel(id=uuid4(), name="Ambiguous", owner_id=ambiguous_owner.id, company_id=None)
    project_no_company = ProjectModel(
        id=uuid4(), name="No Company At All", owner_id=no_company_owner.id, company_id=None
    )
    session.add_all([project_ambiguous, project_no_company])
    session.commit()

    still_null = run_backfill(session.connection())
    session.commit()
    session.refresh(project_ambiguous)
    session.refresh(project_no_company)

    assert project_ambiguous.company_id is None
    assert project_no_company.company_id is None
    assert still_null == count_null_company_id(session.connection())
    assert still_null >= 2
