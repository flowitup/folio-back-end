"""Integration tests: resolver + SqlAlchemyAuthzReader against a real DB session.

Two companies (A, B), several roles, one project per company. Exercises the
full stack — `SqlAlchemyAuthzReader` reading `user_company_access` /
`user_projects` / `projects.company_id`, feeding
`app.domain.authz.resolver.effective_permissions` — for every combination in
the plan's acceptance criteria: admin, manager (assigned/unassigned), member,
a user with no company relationship, and the platform-admin (`*:*`) bypass.

Uses the plain SQLAlchemy `session`/`tables` fixtures from tests/conftest.py
(no Flask app needed — this is repository + domain-resolver wiring, not an
HTTP surface).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.domain.authz.resolver import effective_permissions
from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader

PASSWORD_HASH = "x" * 60  # never verified in these tests


@pytest.fixture
def two_company_world(session):
    """Two companies (A, B), one project each, and a cast of company-role users.

    Layout:
      company_a: admin_a (admin), manager_a_assigned (manager, assigned to
        project_a), manager_a_unassigned (manager, NOT assigned),
        member_a_assigned (member, assigned), member_a_unassigned (member,
        not assigned).
      company_b: admin_b (admin), project_b.
      outsider: no user_company_access row anywhere.
    """
    now = datetime.now(timezone.utc)

    def make_user(email: str) -> UserModel:
        u = UserModel(id=uuid4(), email=email, password_hash=PASSWORD_HASH, is_active=True)
        session.add(u)
        return u

    admin_a = make_user("admin_a@authz-test.com")
    manager_a_assigned = make_user("manager_a_assigned@authz-test.com")
    manager_a_unassigned = make_user("manager_a_unassigned@authz-test.com")
    member_a_assigned = make_user("member_a_assigned@authz-test.com")
    member_a_unassigned = make_user("member_a_unassigned@authz-test.com")
    admin_b = make_user("admin_b@authz-test.com")
    outsider = make_user("outsider@authz-test.com")
    session.flush()

    company_a = CompanyModel(
        id=uuid4(), legal_name="Company A", address="1 rue A", created_by=admin_a.id, created_at=now, updated_at=now
    )
    company_b = CompanyModel(
        id=uuid4(), legal_name="Company B", address="2 rue B", created_by=admin_b.id, created_at=now, updated_at=now
    )
    session.add_all([company_a, company_b])
    session.flush()

    project_a = ProjectModel(id=uuid4(), name="Project A", owner_id=admin_a.id, company_id=company_a.id)
    project_b = ProjectModel(id=uuid4(), name="Project B", owner_id=admin_b.id, company_id=company_b.id)
    session.add_all([project_a, project_b])
    session.flush()

    accesses = [
        UserCompanyAccessModel(user_id=admin_a.id, company_id=company_a.id, role="admin", is_primary=True),
        UserCompanyAccessModel(user_id=manager_a_assigned.id, company_id=company_a.id, role="manager", is_primary=True),
        UserCompanyAccessModel(
            user_id=manager_a_unassigned.id, company_id=company_a.id, role="manager", is_primary=True
        ),
        UserCompanyAccessModel(user_id=member_a_assigned.id, company_id=company_a.id, role="member", is_primary=True),
        UserCompanyAccessModel(user_id=member_a_unassigned.id, company_id=company_a.id, role="member", is_primary=True),
        UserCompanyAccessModel(user_id=admin_b.id, company_id=company_b.id, role="admin", is_primary=True),
    ]
    session.add_all(accesses)
    session.flush()

    # Assign manager_a_assigned + member_a_assigned to project_a only — raw
    # text() insert, matching how the rest of the codebase (and
    # SqlAlchemyProjectMembershipRepository.add) populates user_projects.
    from sqlalchemy import text as _text

    for uid in (manager_a_assigned.id, member_a_assigned.id):
        session.execute(
            _text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
            {"uid": str(uid), "pid": str(project_a.id), "at": now},
        )
    session.commit()

    return {
        "company_a": company_a.id,
        "company_b": company_b.id,
        "project_a": project_a.id,
        "project_b": project_b.id,
        "admin_a": admin_a.id,
        "manager_a_assigned": manager_a_assigned.id,
        "manager_a_unassigned": manager_a_unassigned.id,
        "member_a_assigned": member_a_assigned.id,
        "member_a_unassigned": member_a_unassigned.id,
        "admin_b": admin_b.id,
        "outsider": outsider.id,
    }


@pytest.fixture
def reader(session):
    return SqlAlchemyAuthzReader(session)


# ---------------------------------------------------------------------------
# AuthzReaderPort — direct adapter checks.
# ---------------------------------------------------------------------------


def test_reader_company_role_for(two_company_world, reader):
    w = two_company_world
    assert reader.company_role_for(w["admin_a"], w["company_a"]) == "admin"
    assert reader.company_role_for(w["manager_a_assigned"], w["company_a"]) == "manager"
    assert reader.company_role_for(w["member_a_assigned"], w["company_a"]) == "member"
    assert reader.company_role_for(w["outsider"], w["company_a"]) is None
    # Cross-company: admin_b has no row on company_a.
    assert reader.company_role_for(w["admin_b"], w["company_a"]) is None


def test_reader_is_assigned(two_company_world, reader):
    w = two_company_world
    assert reader.is_assigned(w["manager_a_assigned"], w["project_a"]) is True
    assert reader.is_assigned(w["manager_a_unassigned"], w["project_a"]) is False


def test_reader_project_company_id(two_company_world, reader):
    w = two_company_world
    assert reader.project_company_id(w["project_a"]) == w["company_a"]
    assert reader.project_company_id(uuid4()) is None


def test_reader_admin_company_ids(two_company_world, reader):
    w = two_company_world
    assert reader.admin_company_ids(w["admin_a"]) == [w["company_a"]]
    assert reader.admin_company_ids(w["member_a_assigned"]) == []


def test_reader_primary_company_id(two_company_world, reader):
    w = two_company_world
    assert reader.primary_company_id(w["admin_a"]) == w["company_a"]
    assert reader.primary_company_id(w["outsider"]) is None


def test_reader_matches_rows_regardless_of_insert_path(session, two_company_world):
    """UUID columns are plain CHAR/TEXT on SQLite: an ORM-inserted row (dashless
    hex bind) and a raw-text-inserted row (dashed string literal) must both be
    found by the reader — this is the exact mismatch class the `_norm()` SQL
    normalization in SqlAlchemyAuthzReader exists to prevent. Regression guard
    for the `user_projects` row `POST /projects` creates via
    `SqlAlchemyProjectMembershipRepository.add()` (raw text(), dashed).
    """
    from sqlalchemy import text as _text

    from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader
    from app.infrastructure.database.repositories.sqlalchemy_project_membership import (
        SqlAlchemyProjectMembershipRepository,
    )
    from app.domain.entities.project_membership import ProjectMembership

    w = two_company_world
    membership_repo = SqlAlchemyProjectMembershipRepository(session)
    assert membership_repo.add(ProjectMembership.create(user_id=w["admin_b"], project_id=w["project_b"]))

    reader = SqlAlchemyAuthzReader(session)
    assert reader.is_assigned(w["admin_b"], w["project_b"]) is True

    # Same check, but via a plain raw-text insert with a dashed literal — the
    # two write paths must both be visible to the same reader.
    session.execute(
        _text(
            "INSERT INTO user_company_access (user_id, company_id, role, is_primary, attached_at) VALUES (:uid, :cid, 'member', 0, :at)"
        ),
        {"uid": str(w["outsider"]), "cid": str(w["company_b"]), "at": datetime.now(timezone.utc)},
    )
    session.commit()
    assert reader.company_role_for(w["outsider"], w["company_b"]) == "member"


def test_reader_grants_for_empty_with_no_rows(two_company_world, reader):
    """No company_member_grants row for this user/company → empty list."""
    w = two_company_world
    assert reader.grants_for(w["admin_a"], w["company_a"], w["project_a"]) == []


def test_reader_grants_for_reads_company_member_grants(session, two_company_world, reader):
    """A grant row scoped to project_a applies there and only there (D8)."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel

    w = two_company_world
    session.add(
        CompanyMemberGrantModel(
            id=uuid4(),
            company_id=w["company_a"],
            user_id=w["member_a_assigned"],
            permission="project:manage_labor",
            effect="grant",
            project_id=w["project_a"],
            granted_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    rows = reader.grants_for(w["member_a_assigned"], w["company_a"], w["project_a"])
    assert rows == [("project:manage_labor", "grant")]

    # A different company_id (project_b's) must not see this row.
    assert reader.grants_for(w["member_a_assigned"], w["company_b"], w["project_b"]) == []


def test_reader_grants_for_company_wide_deny_applies_to_every_project(session, two_company_world, reader):
    """A company-wide (project_id NULL) deny row applies regardless of which project is asked about."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
    from app.infrastructure.database.models.project import ProjectModel

    w = two_company_world
    second_project = ProjectModel(id=uuid4(), name="Project A2", owner_id=w["admin_a"], company_id=w["company_a"])
    session.add(second_project)
    session.add(
        CompanyMemberGrantModel(
            id=uuid4(),
            company_id=w["company_a"],
            user_id=w["manager_a_assigned"],
            permission="project:manage_labor",
            effect="deny",
            project_id=None,
            granted_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    # Applies on project_a...
    rows_a = reader.grants_for(w["manager_a_assigned"], w["company_a"], w["project_a"])
    assert rows_a == [("project:manage_labor", "deny")]
    # ...and on a second, unrelated project of the same company.
    rows_b = reader.grants_for(w["manager_a_assigned"], w["company_a"], second_project.id)
    assert rows_b == [("project:manage_labor", "deny")]


def test_resolver_project_scoped_grant_does_not_leak_to_other_project(session, two_company_world, reader):
    """effective_permissions: a grant on project_a must not apply on a second project of the same company."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
    from app.infrastructure.database.models.project import ProjectModel

    w = two_company_world
    other_project = ProjectModel(id=uuid4(), name="Project A3", owner_id=w["admin_a"], company_id=w["company_a"])
    session.add(other_project)
    session.add(
        CompanyMemberGrantModel(
            id=uuid4(),
            company_id=w["company_a"],
            user_id=w["member_a_assigned"],
            permission="project:manage_invoices",
            effect="grant",
            project_id=w["project_a"],
            granted_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    perms_on_granted_project = effective_permissions(reader, w["member_a_assigned"], project_id=w["project_a"])
    assert "project:manage_invoices" in perms_on_granted_project

    # member_a_assigned is not assigned to other_project at all, so the base
    # matrix already denies everything project-scoped there — the grant must
    # not resurrect it.
    perms_on_other_project = effective_permissions(reader, w["member_a_assigned"], project_id=other_project.id)
    assert "project:manage_invoices" not in perms_on_other_project


def test_resolver_deny_wins_over_matrix_grant(session, two_company_world, reader):
    """effective_permissions: a deny row removes a permission the base matrix would otherwise grant."""
    from datetime import datetime, timezone
    from uuid import uuid4

    from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel

    w = two_company_world
    session.add(
        CompanyMemberGrantModel(
            id=uuid4(),
            company_id=w["company_a"],
            user_id=w["manager_a_assigned"],
            permission="project:manage_labor",
            effect="deny",
            project_id=w["project_a"],
            granted_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    perms = effective_permissions(reader, w["manager_a_assigned"], project_id=w["project_a"])
    assert "project:manage_labor" not in perms
    # Everything else the matrix grants a manager stays intact.
    assert "project:manage_invoices" in perms


# ---------------------------------------------------------------------------
# effective_permissions — full resolver stack, per plan acceptance criteria.
# ---------------------------------------------------------------------------


def test_admin_full_rights_on_own_company_project(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["admin_a"], project_id=w["project_a"])
    for p in ("project:create", "project:delete", "project:update", "project:manage_labor", "company:manage_billing"):
        assert p in perms, p


def test_admin_of_a_has_no_rights_on_b(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["admin_a"], project_id=w["project_b"])
    assert perms == frozenset()


def test_manager_assigned_full_project_rights_no_create_delete(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["manager_a_assigned"], project_id=w["project_a"])
    assert "project:manage_labor" in perms
    assert "project:manage_invoices" in perms
    assert "project:create" not in perms
    assert "project:delete" not in perms
    assert not any(p.startswith("company:") for p in perms)


def test_manager_unassigned_loses_project_rights(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["manager_a_unassigned"], project_id=w["project_a"])
    assert perms == frozenset({"user:read"})


def test_member_assigned_read_only_no_pay(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["member_a_assigned"], project_id=w["project_a"])
    assert "project:read" in perms
    assert "project:log_own_attendance" in perms
    assert "project:manage_labor" not in perms
    assert "project:view_pay" not in perms


def test_member_unassigned_has_no_project_scope(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["member_a_unassigned"], project_id=w["project_a"])
    assert perms == frozenset({"user:read"})


def test_user_with_no_company_gets_nothing(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["outsider"], project_id=w["project_a"])
    assert perms == frozenset()


def test_platform_admin_bypasses_everything(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["outsider"], project_id=w["project_a"], is_platform_admin=True)
    assert perms == frozenset({"*:*"})


def test_company_id_only_no_project(two_company_world, reader):
    """Company-only evaluation (no project_id) still resolves admin's full matrix."""
    w = two_company_world
    perms = effective_permissions(reader, w["admin_a"], company_id=w["company_a"])
    assert "company:manage_settings" in perms
    assert "project:create" in perms


def test_neither_project_nor_company_aggregates_admin_companies(two_company_world, reader):
    """M3: `company:*` is never handed out without a resolved company — only
    `project:create` (creation-only) + the universal `user:read`."""
    w = two_company_world
    perms = effective_permissions(reader, w["admin_a"])
    assert perms == frozenset({"user:read", "project:create"})


def test_neither_project_nor_company_non_admin_gets_only_user_read(two_company_world, reader):
    w = two_company_world
    perms = effective_permissions(reader, w["member_a_assigned"])
    assert perms == frozenset({"user:read"})


def test_missing_project_resolves_to_empty_set(two_company_world, reader):
    """A project_id that doesn't exist (deleted / bad id) yields empty, not an error."""
    perms = effective_permissions(reader, two_company_world["admin_a"], project_id=uuid4())
    assert perms == frozenset()
