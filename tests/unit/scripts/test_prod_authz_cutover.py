"""Tenant decisions applied by scripts/prod_authz_cutover.py after the roles migration."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.infrastructure.database.models import UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from scripts.prod_authz_cutover import CutoverError, apply_cutover, snapshot

NOW = datetime.now(timezone.utc)


def _user(session, email: str, ops: bool = False) -> UserModel:
    user = UserModel(id=uuid4(), email=email, is_active=True, is_platform_ops=ops)
    session.add(user)
    return user


def _company(session, created_by, legal_name: str) -> CompanyModel:
    company = CompanyModel(
        id=uuid4(), legal_name=legal_name, address="1 rue", created_by=created_by, created_at=NOW, updated_at=NOW
    )
    session.add(company)
    return company


def _attach(session, user, company, role: str, is_primary: bool = True) -> None:
    session.add(
        UserCompanyAccessModel(
            user_id=user.id, company_id=company.id, role=role, is_primary=is_primary, attached_at=NOW
        )
    )


@pytest.fixture
def tenant(session):
    ops = _user(session, "ops@test.com", ops=True)
    attached = _user(session, "attached@test.com", ops=True)  # migration flagged a legacy admin as ops
    detached = _user(session, "detached@test.com", ops=True)
    demo = _user(session, "demo@test.com")
    session.flush()
    company = _company(session, ops.id, "Tenant Co")
    session.flush()
    _attach(session, ops, company, "admin")
    _attach(session, attached, company, "member")
    session.flush()
    return {"ops": ops, "attached": attached, "detached": detached, "demo": demo, "company": company}


def _profiles(session, user_id) -> int:
    return session.execute(
        text(
            "SELECT count(*) FROM company_persons cp JOIN persons p ON p.id = cp.person_id "
            "WHERE CAST(p.user_id AS TEXT) = :uid AND cp.is_active = 1"
        ),
        {"uid": str(user_id).replace("-", "")},
    ).scalar_one()


def test_cutover_applies_admins_ops_and_deletions(session, tenant):
    report = apply_cutover(
        session.connection(),
        "Tenant Co",
        admins=["attached@test.com", "detached@test.com"],
        ops=["ops@test.com"],
        delete=["demo@test.com"],
    )
    session.expire_all()

    company = tenant["company"]
    assert session.get(UserCompanyAccessModel, (tenant["attached"].id, company.id)).role == "admin"
    detached_access = session.get(UserCompanyAccessModel, (tenant["detached"].id, company.id))
    assert detached_access.role == "admin" and detached_access.is_primary is True
    assert session.get(UserModel, tenant["attached"].id).is_platform_ops is False
    assert session.get(UserModel, tenant["detached"].id).is_platform_ops is False
    assert session.get(UserModel, tenant["ops"].id).is_platform_ops is True
    demo_rows = session.execute(text("SELECT count(*) FROM users WHERE email = 'demo@test.com'")).scalar_one()
    assert demo_rows == 0
    # Every admin attachment is visible in the directory afterwards.
    assert _profiles(session, tenant["detached"].id) == 1
    assert (report.admins_attached, report.admins_raised, report.ops_cleared, report.users_deleted) == (1, 1, 2, 1)
    assert any("detached@test.com attached as admin" in line for line in report.lines)


def test_cutover_is_idempotent(session, tenant):
    args = ("Tenant Co", ["attached@test.com", "detached@test.com"], ["ops@test.com"], [])
    apply_cutover(session.connection(), *args)
    second = apply_cutover(session.connection(), *args)
    assert (second.admins_attached, second.admins_raised, second.ops_cleared, second.ops_set) == (0, 0, 0, 0)
    assert second.profiles_created == 0


def test_unknown_names_fail_before_writing(session, tenant):
    with pytest.raises(CutoverError, match="user not found"):
        apply_cutover(session.connection(), "Tenant Co", ["nobody@test.com"], [], [])
    with pytest.raises(CutoverError, match="company not found"):
        apply_cutover(session.connection(), "Other Co", [], [], [])
    session.expire_all()
    assert session.get(UserModel, tenant["attached"].id).is_platform_ops is True


def test_snapshot_lists_every_user_with_roles(session, tenant):
    lines = snapshot(session.connection())
    assert any(line.startswith("  ops@test.com") and "Tenant Co:admin" in line for line in lines)
    assert any(line.startswith("  detached@test.com") and "(no company)" in line for line in lines)
