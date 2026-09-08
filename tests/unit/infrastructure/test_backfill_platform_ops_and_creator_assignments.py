"""Legacy roles → company tenancy mapping (shared with migration 9a4c1e7b2d05)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from app.domain.authz.resolver import has_permission
from app.infrastructure.database.backfills.platform_ops_and_creator_assignments import run_backfill
from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel
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


def _global_role(session, name: str, permission: "str | None" = None) -> RoleModel:
    role = RoleModel(id=uuid4(), name=name, description=name)
    if permission is not None:
        perm = PermissionModel(id=uuid4(), name=permission, resource=permission.split(":")[0], action="x")
        session.add(perm)
        role.permissions.append(perm)
    session.add(role)
    return role


def _attach(session, user, company, role: str, is_primary: bool = True) -> None:
    session.add(
        UserCompanyAccessModel(
            user_id=user.id, company_id=company.id, role=role, is_primary=is_primary, attached_at=NOW
        )
    )


def test_star_role_becomes_ops_and_admin_of_the_primary_company(session):
    ops_role = _global_role(session, "admin", "*:*")
    ops = _user(session, "ops@test.com")
    ops.roles.append(ops_role)
    session.flush()
    primary = _company(session, ops.id)
    secondary = _company(session, ops.id)
    session.flush()
    _attach(session, ops, primary, "member", is_primary=True)
    _attach(session, ops, secondary, "member", is_primary=False)
    session.flush()

    report = run_backfill(session.connection())
    session.expire_all()  # the backfill writes in SQL; drop the ORM identity map

    assert report.ops_users == 1
    assert session.get(UserModel, ops.id).is_platform_ops is True
    assert session.get(UserCompanyAccessModel, (ops.id, primary.id)).role == "admin"
    # Ops is a support flag, not a tenant role: the other company is untouched.
    assert session.get(UserCompanyAccessModel, (ops.id, secondary.id)).role == "member"


def test_global_manager_is_raised_where_attached_but_never_demotes_an_admin(session):
    manager_role = _global_role(session, "manager")
    user = _user(session, "manager@test.com")
    user.roles.append(manager_role)
    session.flush()
    company_a = _company(session, user.id)
    company_b = _company(session, user.id)
    session.flush()
    _attach(session, user, company_a, "member")
    _attach(session, user, company_b, "admin", is_primary=False)
    session.flush()

    report = run_backfill(session.connection())
    session.expire_all()  # the backfill writes in SQL; drop the ORM identity map

    assert report.global_managers == 1
    assert session.get(UserCompanyAccessModel, (user.id, company_a.id)).role == "manager"
    assert session.get(UserCompanyAccessModel, (user.id, company_b.id)).role == "admin"
    assert session.get(UserModel, user.id).is_platform_ops is False


def test_creator_is_assigned_and_can_read_their_project_afterwards(session):
    _global_role(session, "manager")
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
    ops_role = _global_role(session, "admin", "*:*")
    ops = _user(session, "idempotent@test.com")
    ops.roles.append(ops_role)
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
    assert second.ops_company_admins == 0
    assert second.profiles_created == 0
