"""Legacy global roles → ops flag / company roles (migration 9a4c1e7b2d05).

The mapping runs once, on a database that still has the legacy role tables;
revision c2b8f1a0d743 drops them, so they are no longer part of the models.
The tests recreate that pre-drop shape with plain DDL inside the fixture's
transaction (SQLite rolls DDL back) and run the real functions against it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport
from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from scripts.migration_legacy_role_mapping import backfill_global_managers, backfill_platform_ops

_LEGACY_TABLES = ("permissions", "roles", "role_permissions", "user_roles")

_LEGACY_DDL = (
    "CREATE TABLE IF NOT EXISTS permissions (id VARCHAR(32) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL)",
    "CREATE TABLE IF NOT EXISTS roles (id VARCHAR(32) NOT NULL PRIMARY KEY, name VARCHAR(50) NOT NULL)",
    "CREATE TABLE IF NOT EXISTS role_permissions (role_id VARCHAR(32) NOT NULL, permission_id VARCHAR(32) NOT NULL)",
    "CREATE TABLE IF NOT EXISTS user_roles (user_id VARCHAR(32) NOT NULL, role_id VARCHAR(32) NOT NULL)",
)


@pytest.fixture
def legacy_tables(session):
    """The four tables migration c2b8f1a0d743 drops, as they were before it.

    SQLite commits implicitly on DDL, so the tables outlive the fixture's
    transaction: create them once and start every test from empty ones.
    """
    for statement in _LEGACY_DDL:
        session.execute(text(statement))
    for table in _LEGACY_TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    return session


def _sql_id(value: UUID) -> str:
    """Ids are stored dashless on SQLite — write the legacy rows the same way."""
    return value.hex


def _user(session, email: str) -> UserModel:
    user = UserModel(id=uuid4(), email=email, is_active=True)
    session.add(user)
    return user


def _company(session, created_by: UUID) -> CompanyModel:
    now = datetime.now(timezone.utc)
    company = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(company)
    return company


def _attach(session, user, company, role: str, is_primary: bool = True) -> None:
    session.add(
        UserCompanyAccessModel(
            user_id=user.id,
            company_id=company.id,
            role=role,
            is_primary=is_primary,
            attached_at=datetime.now(timezone.utc),
        )
    )


def _global_role(session, name: str, permission: str | None = None) -> UUID:
    role_id = uuid4()
    session.execute(text("INSERT INTO roles (id, name) VALUES (:id, :name)"), {"id": _sql_id(role_id), "name": name})
    if permission is not None:
        permission_id = uuid4()
        session.execute(
            text("INSERT INTO permissions (id, name) VALUES (:id, :name)"),
            {"id": _sql_id(permission_id), "name": permission},
        )
        session.execute(
            text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
            {"r": _sql_id(role_id), "p": _sql_id(permission_id)},
        )
    return role_id


def _hold(session, user, role_id: UUID) -> None:
    session.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
        {"u": _sql_id(user.id), "r": _sql_id(role_id)},
    )


def test_star_role_becomes_ops_and_admin_of_the_primary_company(legacy_tables):
    session = legacy_tables
    ops = _user(session, f"ops-{uuid4().hex[:8]}@test.com")
    session.flush()
    ops_role = _global_role(session, "admin", "*:*")
    _hold(session, ops, ops_role)
    primary = _company(session, ops.id)
    secondary = _company(session, ops.id)
    session.flush()
    _attach(session, ops, primary, "member", is_primary=True)
    _attach(session, ops, secondary, "member", is_primary=False)
    session.flush()

    report = BackfillReport()
    backfill_platform_ops(session.connection(), report)
    session.expire_all()  # the backfill writes in SQL; drop the ORM identity map

    assert report.ops_users == 1
    assert session.get(UserModel, ops.id).is_platform_ops is True
    assert session.get(UserCompanyAccessModel, (ops.id, primary.id)).role == "admin"
    # Ops is a support flag, not a tenant role: the other company is untouched.
    assert session.get(UserCompanyAccessModel, (ops.id, secondary.id)).role == "member"


def test_global_manager_is_raised_where_attached_but_never_demotes_an_admin(legacy_tables):
    session = legacy_tables
    user = _user(session, f"legacy-manager-{uuid4().hex[:8]}@test.com")
    session.flush()
    manager_role = _global_role(session, "manager")
    _hold(session, user, manager_role)
    company_a = _company(session, user.id)
    company_b = _company(session, user.id)
    session.flush()
    _attach(session, user, company_a, "member")
    _attach(session, user, company_b, "admin", is_primary=False)
    session.flush()

    report = BackfillReport()
    backfill_global_managers(session.connection(), report)
    session.expire_all()

    assert report.global_managers == 1
    assert session.get(UserCompanyAccessModel, (user.id, company_a.id)).role == "manager"
    assert session.get(UserCompanyAccessModel, (user.id, company_b.id)).role == "admin"
    assert session.get(UserModel, user.id).is_platform_ops is False


def test_both_steps_are_idempotent(legacy_tables):
    session = legacy_tables
    ops = _user(session, f"legacy-idem-{uuid4().hex[:8]}@test.com")
    session.flush()
    _hold(session, ops, _global_role(session, "admin", "*:*"))
    _hold(session, ops, _global_role(session, "manager"))
    company = _company(session, ops.id)
    session.flush()
    _attach(session, ops, company, "member")
    session.flush()

    first, second = BackfillReport(), BackfillReport()
    for report in (first, second):
        backfill_platform_ops(session.connection(), report)
        backfill_global_managers(session.connection(), report)
    session.expire_all()

    assert first.ops_company_admins == 1
    assert second.ops_company_admins == 0
    assert second.global_managers == 0
    assert session.get(UserCompanyAccessModel, (ops.id, company.id)).role == "admin"
