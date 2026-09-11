"""H3 — a D8 deny row must win over the legacy JWT-claim union.

Before this fix, `app.api.v1.projects.decorators._effective_permissions` /
`_effective_perms_for` unioned the legacy global-role permission set with the
resolver's ALLOWED output but never consulted the resolver's DENY rows — so
an admin-managed deny on a manager/member could never override a permission
a legacy global role happened to also carry (D8's "deny always wins" was
silently defeated for anyone still holding a legacy role).

This user holds BOTH: a legacy global "manager" role (JWT claim carries
`project:manage_labor` directly) AND the company-tenant "manager" role
assigned to the project (resolver ALSO grants `project:manage_labor`). A D8
deny row on that exact permission must still remove it from both sources.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from tests.auth_login_helper import mint_access_token


@pytest.fixture(scope="module")
def deny_app():
    """One company, one project, one user who is BOTH a legacy global
    "manager" (JWT claim carries project:manage_labor) and the company-tenant
    "manager" assigned to the project (resolver also grants it)."""
    from app import create_app, db
    from config import TestingConfig

    class DenyTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(DenyTestConfig)

    with test_app.app_context():
        db.create_all()
        now = datetime.now(timezone.utc)

        user = UserModel(email="dw_manager@test.com", is_active=True)
        db.session.add(user)
        db.session.flush()

        company = CompanyModel(
            id=uuid4(), legal_name="Deny Co", address="1 rue Deny", created_by=user.id, created_at=now, updated_at=now
        )
        db.session.add(company)
        db.session.flush()

        project = ProjectModel(name="Deny Project", owner_id=user.id, company_id=company.id)
        db.session.add(project)
        db.session.flush()

        db.session.add(
            UserCompanyAccessModel(
                user_id=user.id, company_id=company.id, role="manager", is_primary=True, attached_at=now
            )
        )
        db.session.commit()

        from sqlalchemy import text as _text

        # Assigned to the project: the company `manager` role grants
        # project:manage_labor here, so the deny row has something to override.
        db.session.execute(
            _text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
            {"uid": str(user.id), "pid": str(project.id), "at": now},
        )
        db.session.commit()

        test_app._user_id = user.id
        test_app._project_id = project.id

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(deny_app):
    return deny_app.test_client()


@pytest.fixture
def manager_h(client):
    return {"Authorization": f"Bearer {mint_access_token(client, 'dw_manager@test.com')}"}


@pytest.fixture
def denied_manage_labor(deny_app, monkeypatch):
    """Monkeypatch the wired authz_reader so grants_for returns a deny row
    for project:manage_labor on this user/project, as if a company admin
    had just written a D8 deny row (Phase 2 storage doesn't exist yet)."""
    from wiring import get_container

    with deny_app.app_context():
        reader = get_container().authz_reader

        def _fake_grants_for(user_id, company_id, project_id):
            return [("project:manage_labor", "deny")]

        monkeypatch.setattr(reader, "grants_for", _fake_grants_for)
    yield


def test_legacy_role_permission_confirmed_present_before_deny(client, manager_h, deny_app):
    """Sanity check: without the deny, both the legacy claim and the resolver
    grant project:manage_labor (proves the deny test below isn't vacuous)."""
    resp = client.get(f"/api/v1/projects/{deny_app._project_id}", headers=manager_h)
    assert resp.status_code == 200
    assert "project:manage_labor" in resp.get_json()["my_permissions"]


def test_deny_removes_permission_despite_legacy_global_role(client, manager_h, deny_app, denied_manage_labor):
    resp = client.get(f"/api/v1/projects/{deny_app._project_id}", headers=manager_h)
    assert resp.status_code == 200
    perms = resp.get_json()["my_permissions"]
    assert "project:manage_labor" not in perms
    # Scoped, not a blanket wipe — read access (NON_DENIABLE) survives.
    assert "project:read" in perms


def test_deny_causes_403_on_log_attendance_route(client, manager_h, deny_app, denied_manage_labor):
    resp = client.post(
        f"/api/v1/projects/{deny_app._project_id}/labor-entries",
        json={"worker_id": str(uuid4()), "date": "2026-06-01", "shift_type": "full"},
        headers=manager_h,
    )
    assert resp.status_code == 403
