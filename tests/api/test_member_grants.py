"""API tests for the D8 member-grants endpoints (per-user permission customisation).

Covers `GET/PUT/DELETE /companies/<company_id>/members/<user_id>/grants`
(`app.api.v1.companies.grants_routes`) and the resolver-visible effect of a
grant/deny row on real project routes and `GET /projects/<id>`'s
`my_permissions` — proving the whole chain: admin writes a row →
`SqlAlchemyAuthzReader.grants_for` reads it → `effective_permissions` folds it
in → the gated route reflects it, all on the very next request (no re-login).

Fixture layout:
  - Company A: admin_a (admin), admin_target_a (a second admin — D8 forbids
    customising admins), manager_a (manager, assigned to projects P and Q),
    member_a (member, assigned to P and Q), stranger (no access row at all).
  - Company B: admin_b (admin), project_r (used to prove a project from a
    DIFFERENT company is rejected).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel
from app.infrastructure.database.models.worker import WorkerModel
from tests.auth_login_helper import mint_access_token

PASSWORD = "Passw0rd!"


@pytest.fixture(scope="module")
def mg_app():
    """Two companies, admin/manager/member/stranger users, projects P/Q (company A) + R (company B)."""
    from app import create_app, db
    from config import TestingConfig

    class MgTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(MgTestConfig)

    with test_app.app_context():
        db.create_all()

        now = datetime.now(timezone.utc)

        # Neutral legacy role: zero permissions, exists only so `user_projects`
        db.session.flush()

        def _user(email: str) -> UserModel:
            u = UserModel(email=email, is_active=True)
            db.session.add(u)
            return u

        admin_a = _user("mg_admin_a@test.com")
        admin_target_a = _user("mg_admin_target_a@test.com")
        manager_a = _user("mg_manager_a@test.com")
        member_a = _user("mg_member_a@test.com")
        stranger = _user("mg_stranger@test.com")
        admin_b = _user("mg_admin_b@test.com")
        db.session.flush()

        def _company(name: str, created_by: UUID) -> CompanyModel:
            c = CompanyModel(
                id=uuid4(),
                legal_name=name,
                address="1 rue de la Paix",
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            db.session.add(c)
            return c

        company_a = _company("MG Company A", admin_a.id)
        company_b = _company("MG Company B", admin_b.id)
        db.session.flush()

        def _access(user_id: UUID, company_id: UUID, role: str) -> None:
            db.session.add(
                UserCompanyAccessModel(
                    user_id=user_id, company_id=company_id, role=role, is_primary=True, attached_at=now
                )
            )

        _access(admin_a.id, company_a.id, "admin")
        _access(admin_target_a.id, company_a.id, "admin")
        _access(manager_a.id, company_a.id, "manager")
        _access(member_a.id, company_a.id, "member")
        _access(admin_b.id, company_b.id, "admin")
        db.session.flush()

        project_p = ProjectModel(name="Project P", owner_id=admin_a.id, company_id=company_a.id)
        project_q = ProjectModel(name="Project Q", owner_id=admin_a.id, company_id=company_a.id)
        project_r = ProjectModel(name="Project R", owner_id=admin_b.id, company_id=company_b.id)
        db.session.add_all([project_p, project_q, project_r])
        db.session.flush()

        worker_p = WorkerModel(project_id=project_p.id, name="Worker P", daily_rate=100)
        worker_q = WorkerModel(project_id=project_q.id, name="Worker Q", daily_rate=100)
        db.session.add_all([worker_p, worker_q])
        db.session.flush()

        from sqlalchemy import text as _text

        def _assign(user_id: UUID, project_id: UUID) -> None:
            db.session.execute(
                _text("INSERT INTO user_projects (user_id, project_id, assigned_at) VALUES (:uid, :pid, :at)"),
                {"uid": str(user_id), "pid": str(project_id), "at": now},
            )

        _assign(manager_a.id, project_p.id)
        _assign(manager_a.id, project_q.id)
        _assign(member_a.id, project_p.id)
        _assign(member_a.id, project_q.id)

        db.session.commit()

        test_app._company_a_id = str(company_a.id)
        test_app._company_b_id = str(company_b.id)
        test_app._project_p_id = str(project_p.id)
        test_app._project_q_id = str(project_q.id)
        test_app._project_r_id = str(project_r.id)
        test_app._worker_p_id = str(worker_p.id)
        test_app._worker_q_id = str(worker_q.id)
        test_app._admin_a_email = "mg_admin_a@test.com"
        test_app._admin_target_a_id = str(admin_target_a.id)
        test_app._manager_a_email = "mg_manager_a@test.com"
        test_app._manager_a_id = str(manager_a.id)
        test_app._member_a_email = "mg_member_a@test.com"
        test_app._member_a_id = str(member_a.id)
        test_app._stranger_id = str(stranger.id)
        test_app._admin_b_email = "mg_admin_b@test.com"

        db.session.expunge_all()

        yield test_app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(mg_app):
    return mg_app.test_client()


def _login(client, email: str) -> dict:
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


@pytest.fixture
def admin_a_h(client, mg_app):
    return _login(client, mg_app._admin_a_email)


@pytest.fixture
def manager_a_h(client, mg_app):
    return _login(client, mg_app._manager_a_email)


@pytest.fixture
def member_a_h(client, mg_app):
    return _login(client, mg_app._member_a_email)


@pytest.fixture
def admin_b_h(client, mg_app):
    return _login(client, mg_app._admin_b_email)


def _grants_url(mg_app, user_id: str) -> str:
    return f"/api/v1/companies/{mg_app._company_a_id}/members/{user_id}/grants"


def _set_grant(client, admin_h, mg_app, user_id: str, permission: str, effect: str, project_id: str = None) -> "tuple":
    body = {"permission": permission, "effect": effect}
    if project_id is not None:
        body["project_id"] = project_id
    resp = client.put(_grants_url(mg_app, user_id), json=body, headers=admin_h)
    return resp


def _remove_grant(client, admin_h, mg_app, user_id: str, permission: str, project_id: str = None):
    body = {"permission": permission}
    if project_id is not None:
        body["project_id"] = project_id
    return client.delete(_grants_url(mg_app, user_id), json=body, headers=admin_h)


def _create_invoice(client, headers, project_id: str, recipient: str = "ACME"):
    return client.post(
        f"/api/v1/projects/{project_id}/invoices",
        json={
            "type": "others",
            "issue_date": "2026-06-01",
            "recipient_name": recipient,
            "items": [{"description": "misc", "quantity": 1, "unit_price": 10.0, "vat_rate": 0}],
        },
        headers=headers,
    )


def _set_day_description(client, headers, project_id: str, date: str = "2026-06-01"):
    return client.put(
        f"/api/v1/projects/{project_id}/labor-day-descriptions",
        json={"date": date, "description": "worked hard"},
        headers=headers,
    )


def _log_attendance(client, headers, project_id: str, worker_id: str, date: str):
    return client.post(
        f"/api/v1/projects/{project_id}/labor-entries",
        json={"worker_id": worker_id, "date": date, "shift_type": "full"},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Grant: project-scoped grant allows on P, 403 on Q
# ---------------------------------------------------------------------------


class TestProjectScopedGrant:
    def test_grant_allows_on_granted_project_only(self, client, mg_app, admin_a_h, member_a_h):
        # Sanity: a plain member cannot create invoices anywhere (read-only matrix).
        resp = _create_invoice(client, member_a_h, mg_app._project_p_id, recipient="Before Grant")
        assert resp.status_code == 403

        set_resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", "grant", mg_app._project_p_id
        )
        assert set_resp.status_code == 200, set_resp.get_data(as_text=True)
        body = set_resp.get_json()
        assert body["permission"] == "project:manage_invoices"
        assert body["effect"] == "grant"
        assert body["project_id"] == mg_app._project_p_id

        # The invoice-write routes ALSO gate on `require_project_access(write=True)`
        # (`can_mutate_project`: owner, or resolver `project:update`, independent of
        # `project:manage_invoices` — pre-existing route behavior, out of this
        # slice's file ownership). A member is granted `project:update` too so the
        # scenario is realistic: an admin elevating a member's invoicing capability
        # on one project grants both.
        _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:update", "grant", mg_app._project_p_id)

        allowed = _create_invoice(client, member_a_h, mg_app._project_p_id, recipient="On P")
        assert allowed.status_code == 201, allowed.get_data(as_text=True)

        denied_elsewhere = _create_invoice(client, member_a_h, mg_app._project_q_id, recipient="On Q")
        assert denied_elsewhere.status_code == 403

        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", mg_app._project_p_id)
        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:update", mg_app._project_p_id)


# ---------------------------------------------------------------------------
# Deny: project-scoped deny removes on P, leaves Q untouched
# ---------------------------------------------------------------------------


class TestProjectScopedDeny:
    def test_deny_removes_on_denied_project_only(self, client, mg_app, admin_a_h, manager_a_h):
        # Sanity: a manager holds manage_labor on every assigned project by default.
        before_p = _set_day_description(client, manager_a_h, mg_app._project_p_id, date="2026-07-01")
        assert before_p.status_code == 200
        before_q = _set_day_description(client, manager_a_h, mg_app._project_q_id, date="2026-07-01")
        assert before_q.status_code == 200

        set_resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor", "deny", mg_app._project_p_id
        )
        assert set_resp.status_code == 200, set_resp.get_data(as_text=True)

        denied_on_p = _set_day_description(client, manager_a_h, mg_app._project_p_id, date="2026-07-02")
        assert denied_on_p.status_code == 403

        still_allowed_on_q = _set_day_description(client, manager_a_h, mg_app._project_q_id, date="2026-07-02")
        assert still_allowed_on_q.status_code == 200

        # cleanup for later tests in this module-scoped app
        _remove_grant(client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor", mg_app._project_p_id)


# ---------------------------------------------------------------------------
# Company-wide deny applies on every project
# ---------------------------------------------------------------------------


class TestCompanyWideDeny:
    def test_company_wide_deny_applies_everywhere(self, client, mg_app, admin_a_h, manager_a_h):
        set_resp = _set_grant(client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor", "deny")
        assert set_resp.status_code == 200
        assert set_resp.get_json()["project_id"] is None

        denied_on_p = _set_day_description(client, manager_a_h, mg_app._project_p_id, date="2026-07-03")
        assert denied_on_p.status_code == 403
        denied_on_q = _set_day_description(client, manager_a_h, mg_app._project_q_id, date="2026-07-03")
        assert denied_on_q.status_code == 403

        _remove_grant(client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor")


# ---------------------------------------------------------------------------
# Deny wins over a project-level grant
# ---------------------------------------------------------------------------


class TestDenyWinsOverGrant:
    def test_company_deny_beats_project_grant(self, client, mg_app, admin_a_h, member_a_h):
        # member_a holds no project:manage_labor by default (read-only matrix) —
        # a bare project-level grant would normally unlock it on P.
        company_wide_deny = _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor", "deny")
        assert company_wide_deny.status_code == 200

        project_grant = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor", "grant", mg_app._project_p_id
        )
        assert project_grant.status_code == 200

        resp = _set_day_description(
            client, _login(client, mg_app._member_a_email), mg_app._project_p_id, date="2026-07-04"
        )
        assert resp.status_code == 403

        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor")
        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor", mg_app._project_p_id)


# ---------------------------------------------------------------------------
# Validation: forbidden whitelist entries, non-deniable, target role, project scope
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("permission", ["company:manage_members", "project:create", "project:delete", "*:*"])
    def test_forbidden_permission_400(self, client, mg_app, admin_a_h, permission):
        resp = _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, permission, "grant")
        assert resp.status_code == 400, resp.get_data(as_text=True)

    def test_deny_project_read_400(self, client, mg_app, admin_a_h):
        resp = _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:read", "deny")
        assert resp.status_code == 400

    def test_target_admin_400(self, client, mg_app, admin_a_h):
        resp = _set_grant(client, admin_a_h, mg_app, mg_app._admin_target_a_id, "project:manage_invoices", "grant")
        assert resp.status_code == 400

    def test_target_stranger_404(self, client, mg_app, admin_a_h):
        resp = _set_grant(client, admin_a_h, mg_app, mg_app._stranger_id, "project:manage_invoices", "grant")
        assert resp.status_code == 404

    def test_other_company_project_id_404(self, client, mg_app, admin_a_h):
        resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", "grant", mg_app._project_r_id
        )
        assert resp.status_code == 404

    def test_nonexistent_project_id_404(self, client, mg_app, admin_a_h):
        resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", "grant", str(uuid4())
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-company access
# ---------------------------------------------------------------------------


class TestCrossCompanyAccess:
    def test_admin_of_company_a_403_on_company_b(self, client, mg_app, admin_a_h):
        resp = client.get(
            f"/api/v1/companies/{mg_app._company_b_id}/members/{mg_app._manager_a_id}/grants",
            headers=admin_a_h,
        )
        assert resp.status_code == 403

    def test_member_cannot_manage_grants(self, client, mg_app, member_a_h):
        resp = client.get(_grants_url(mg_app, mg_app._manager_a_id), headers=member_a_h)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Removal takes effect on the next request; no re-login required
# ---------------------------------------------------------------------------


class TestRemoval:
    def test_removed_grant_stops_applying_immediately(self, client, mg_app, admin_a_h):
        member_h = _login(client, mg_app._member_a_email)

        set_resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor", "grant", mg_app._project_q_id
        )
        assert set_resp.status_code == 200

        allowed = _set_day_description(client, member_h, mg_app._project_q_id, date="2026-07-05")
        assert allowed.status_code == 200, allowed.get_data(as_text=True)

        del_resp = _remove_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_labor", mg_app._project_q_id
        )
        assert del_resp.status_code == 204

        no_longer_allowed = _set_day_description(client, member_h, mg_app._project_q_id, date="2026-07-06")
        assert no_longer_allowed.status_code == 403

    def test_remove_nonexistent_row_404(self, client, mg_app, admin_a_h):
        resp = _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Idempotent upsert: re-PUT on the same key replaces, never duplicates
# ---------------------------------------------------------------------------


class TestIdempotentUpsert:
    def test_changing_effect_on_same_key_replaces_row(self, client, mg_app, admin_a_h):
        _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", "grant", mg_app._project_p_id)
        _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", "deny", mg_app._project_p_id)

        listing = client.get(_grants_url(mg_app, mg_app._member_a_id), headers=admin_a_h)
        assert listing.status_code == 200
        rows = [
            g
            for g in listing.get_json()["grants"]
            if g["permission"] == "project:invite" and g["project_id"] == mg_app._project_p_id
        ]
        assert len(rows) == 1
        assert rows[0]["effect"] == "deny"

        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", mg_app._project_p_id)


# ---------------------------------------------------------------------------
# GET /projects/<id>'s my_permissions reflects grants/denies
# ---------------------------------------------------------------------------


class TestMyPermissionsReflectsGrants:
    def test_my_permissions_updates_with_grant_and_removal(self, client, mg_app, admin_a_h):
        member_h = _login(client, mg_app._member_a_email)

        before = client.get(f"/api/v1/projects/{mg_app._project_p_id}", headers=member_h)
        assert before.status_code == 200
        assert "project:manage_invoices" not in before.get_json()["my_permissions"]

        _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", "grant", mg_app._project_p_id
        )

        during = client.get(f"/api/v1/projects/{mg_app._project_p_id}", headers=member_h)
        assert during.status_code == 200
        assert "project:manage_invoices" in during.get_json()["my_permissions"]

        _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:manage_invoices", mg_app._project_p_id)

        after = client.get(f"/api/v1/projects/{mg_app._project_p_id}", headers=member_h)
        assert after.status_code == 200
        assert "project:manage_invoices" not in after.get_json()["my_permissions"]


# ---------------------------------------------------------------------------
# Real labor-entries route (worker-backed), matching the phase spec's example
# ---------------------------------------------------------------------------


class TestLaborEntriesDenyExample:
    def test_deny_manage_labor_blocks_log_attendance_on_p_only(self, client, mg_app, admin_a_h, manager_a_h):
        allowed_before = _log_attendance(client, manager_a_h, mg_app._project_p_id, mg_app._worker_p_id, "2026-08-01")
        assert allowed_before.status_code == 201, allowed_before.get_data(as_text=True)

        _set_grant(
            client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor", "deny", mg_app._project_p_id
        )

        denied_on_p = _log_attendance(client, manager_a_h, mg_app._project_p_id, mg_app._worker_p_id, "2026-08-02")
        assert denied_on_p.status_code == 403

        allowed_on_q = _log_attendance(client, manager_a_h, mg_app._project_q_id, mg_app._worker_q_id, "2026-08-02")
        assert allowed_on_q.status_code == 201, allowed_on_q.get_data(as_text=True)

        _remove_grant(client, admin_a_h, mg_app, mg_app._manager_a_id, "project:manage_labor", mg_app._project_p_id)


# ---------------------------------------------------------------------------
# Persistence: the write path must COMMIT, not just flush. Under the shared
# test session a flushed-but-uncommitted row is still visible to the next
# request, which is exactly how the missing commit slipped through on
# Postgres (rows vanished at request teardown). Rolling the session back
# between the write and the read reproduces that teardown.
# ---------------------------------------------------------------------------


class TestGrantWritesAreCommitted:
    def test_put_survives_session_rollback(self, client, mg_app, admin_a_h):
        from app import db

        resp = _set_grant(
            client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", "grant", mg_app._project_p_id
        )
        assert resp.status_code == 200
        db.session.rollback()

        listing = client.get(_grants_url(mg_app, mg_app._member_a_id), headers=admin_a_h)
        rows = [g for g in listing.get_json()["grants"] if g["permission"] == "project:invite"]
        assert rows and rows[0]["project_id"] == mg_app._project_p_id

    def test_delete_survives_session_rollback(self, client, mg_app, admin_a_h):
        from app import db

        _set_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", "grant", mg_app._project_p_id)
        resp = _remove_grant(client, admin_a_h, mg_app, mg_app._member_a_id, "project:invite", mg_app._project_p_id)
        assert resp.status_code == 204
        db.session.rollback()

        listing = client.get(_grants_url(mg_app, mg_app._member_a_id), headers=admin_a_h)
        assert not [g for g in listing.get_json()["grants"] if g["permission"] == "project:invite"]
