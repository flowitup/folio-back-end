"""API matrix: the resolver is the only permission authority (no owner bypass, no JWT claim).

Personas built on the shared `invitation_app` fixture (company "Invite Test
Company", project P1):
  - company admin  → implicit on every project of the company
  - manager        → assigned to P1
  - member         → assigned to P1, read-only
  - grantee        → member with a D8 grant of `project:manage_invoices` on P1
  - denied manager → manager with a D8 deny of `project:manage_labor` on P1
  - lone owner     → owns a project but holds no company role (D6 regression)

Covers D2 (manager cannot delete a project), D6 (owner bypass removed), the
resource-aware secondary write gate, and cross-company isolation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

PASSWORD = "Persona1234!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["access_token"]


@pytest.fixture(scope="module")
def personas(invitation_app):
    """Seed the personas above into the shared fixture app; returns their ids."""
    from app import db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from app.infrastructure.database.models import ProjectModel, UserModel
    from app.infrastructure.database.models.company import CompanyModel
    from app.infrastructure.database.models.company_member_grant import CompanyMemberGrantModel
    from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

    hasher = Argon2PasswordHasher()
    now = datetime.now(timezone.utc)
    company_id = UUID(invitation_app._test_company_id)
    project_id = UUID(invitation_app._test_project_id)
    admin_user_id = UUID(invitation_app._test_admin_user_id)

    with invitation_app.app_context():

        def user(email: str) -> UserModel:
            u = UserModel(id=uuid4(), email=email, password_hash=hasher.hash(PASSWORD), is_active=True)
            db.session.add(u)
            return u

        manager = user("persona_manager@test.com")
        grantee = user("persona_grantee@test.com")
        denied = user("persona_denied@test.com")
        lone_owner = user("persona_lone_owner@test.com")
        other_admin = user("persona_other_admin@test.com")
        db.session.flush()

        other_company = CompanyModel(
            id=uuid4(),
            legal_name="Other Company",
            address="9 rue Ailleurs",
            created_by=other_admin.id,
            created_at=now,
            updated_at=now,
        )
        db.session.add(other_company)
        db.session.flush()

        other_project = ProjectModel(
            id=uuid4(), name="Other Project", owner_id=other_admin.id, company_id=other_company.id
        )
        # A project whose owner holds no company role at all: the owner bypass
        # used to make this readable; it must not any more (D6).
        orphan_project = ProjectModel(
            id=uuid4(), name="Lone Owner Project", owner_id=lone_owner.id, company_id=company_id
        )
        db.session.add_all([other_project, orphan_project])

        db.session.add_all(
            [
                UserCompanyAccessModel(
                    user_id=manager.id, company_id=company_id, role="manager", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=grantee.id, company_id=company_id, role="member", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=denied.id, company_id=company_id, role="manager", is_primary=True, attached_at=now
                ),
                UserCompanyAccessModel(
                    user_id=other_admin.id,
                    company_id=other_company.id,
                    role="admin",
                    is_primary=True,
                    attached_at=now,
                ),
            ]
        )
        db.session.add_all(
            [
                CompanyMemberGrantModel(
                    id=uuid4(),
                    company_id=company_id,
                    user_id=grantee.id,
                    permission="project:manage_invoices",
                    effect="grant",
                    project_id=project_id,
                    granted_by_user_id=admin_user_id,
                    granted_at=now,
                ),
                CompanyMemberGrantModel(
                    id=uuid4(),
                    company_id=company_id,
                    user_id=denied.id,
                    permission="project:manage_labor",
                    effect="deny",
                    project_id=project_id,
                    granted_by_user_id=admin_user_id,
                    granted_at=now,
                ),
            ]
        )
        db.session.commit()

        # Assignments (role_id deliberately NULL: per-project roles grant nothing now).
        from app.infrastructure.database.models.associations import user_projects

        for uid in (manager.id, grantee.id, denied.id):
            db.session.execute(
                user_projects.insert().values(user_id=uid, project_id=project_id, role_id=None, assigned_at=now)
            )
        db.session.commit()

        ids = {
            "manager": str(manager.id),
            "grantee": str(grantee.id),
            "denied": str(denied.id),
            "lone_owner": str(lone_owner.id),
            "other_admin": str(other_admin.id),
            "other_project": str(other_project.id),
            "orphan_project": str(orphan_project.id),
            "other_company": str(other_company.id),
        }
    return ids


@pytest.fixture
def manager_token(inv_client, personas):
    return _login(inv_client, "persona_manager@test.com")


@pytest.fixture
def grantee_token(inv_client, personas):
    return _login(inv_client, "persona_grantee@test.com")


@pytest.fixture
def denied_token(inv_client, personas):
    return _login(inv_client, "persona_denied@test.com")


@pytest.fixture
def lone_owner_token(inv_client, personas):
    return _login(inv_client, "persona_lone_owner@test.com")


@pytest.fixture
def other_admin_token(inv_client, personas):
    return _login(inv_client, "persona_other_admin@test.com")


def _invoice_body():
    return {
        "type": "released_funds",
        "issue_date": date.today().isoformat(),
        "recipient_name": "ACME Corp",
        "items": [{"description": "Work", "quantity": 1, "unit_price": 100}],
    }


def _create_invoice(client, token, project_id) -> str:
    resp = client.post(f"/api/v1/projects/{project_id}/invoices", json=_invoice_body(), headers=_auth(token))
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ---------------------------------------------------------------------------
# Manager on an assigned project
# ---------------------------------------------------------------------------


class TestAssignedManager:
    def test_reads_and_updates_the_project(self, inv_client, invitation_app, manager_token):
        pid = invitation_app._test_project_id
        assert inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(manager_token)).status_code == 200
        resp = inv_client.put(
            f"/api/v1/projects/{pid}", json={"name": "Renamed by manager"}, headers=_auth(manager_token)
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_cannot_delete_the_project(self, inv_client, invitation_app, manager_token):
        """D2: deletion is admin-only, even for an assigned manager."""
        pid = invitation_app._test_project_id
        resp = inv_client.delete(f"/api/v1/projects/{pid}", headers=_auth(manager_token))
        assert resp.status_code == 403

    def test_writes_invoices_on_the_assigned_project(self, inv_client, invitation_app, manager_token):
        pid = invitation_app._test_project_id
        invoice_id = _create_invoice(inv_client, manager_token, pid)
        resp = inv_client.put(
            f"/api/v1/projects/{pid}/invoices/{invoice_id}",
            json={"recipient_name": "ACME updated"},
            headers=_auth(manager_token),
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)

    def test_gets_403_on_a_project_of_another_company(self, inv_client, personas, manager_token):
        resp = inv_client.get(f"/api/v1/projects/{personas['other_project']}", headers=_auth(manager_token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Member (read-only) and D8 grants
# ---------------------------------------------------------------------------


class TestMemberAndGrants:
    def test_member_reads_but_cannot_write_invoices(self, inv_client, invitation_app, member_token):
        pid = invitation_app._test_project_id
        assert inv_client.get(f"/api/v1/projects/{pid}", headers=_auth(member_token)).status_code == 200
        resp = inv_client.post(f"/api/v1/projects/{pid}/invoices", json=_invoice_body(), headers=_auth(member_token))
        assert resp.status_code == 403

    def test_member_cannot_write_labor(self, inv_client, invitation_app, member_token):
        pid = invitation_app._test_project_id
        resp = inv_client.post(
            f"/api/v1/projects/{pid}/labor-entries",
            json={"worker_id": str(uuid4()), "date": date.today().isoformat()},
            headers=_auth(member_token),
        )
        assert resp.status_code == 403

    def test_grant_unlocks_exactly_its_resource(self, inv_client, invitation_app, grantee_token):
        """A member granted `project:manage_invoices` writes invoices — and nothing else."""
        pid = invitation_app._test_project_id
        invoice_id = _create_invoice(inv_client, grantee_token, pid)
        assert (
            inv_client.put(
                f"/api/v1/projects/{pid}/invoices/{invoice_id}",
                json={"recipient_name": "Granted update"},
                headers=_auth(grantee_token),
            ).status_code
            == 200
        )
        # The same grant must not unlock project settings or labor.
        assert (
            inv_client.put(f"/api/v1/projects/{pid}", json={"name": "nope"}, headers=_auth(grantee_token)).status_code
            == 403
        )
        assert (
            inv_client.post(
                f"/api/v1/projects/{pid}/labor-entries",
                json={"worker_id": str(uuid4()), "date": date.today().isoformat()},
                headers=_auth(grantee_token),
            ).status_code
            == 403
        )

    def test_deny_beats_the_manager_role(self, inv_client, invitation_app, denied_token):
        pid = invitation_app._test_project_id
        resp = inv_client.post(
            f"/api/v1/projects/{pid}/labor-entries",
            json={"worker_id": str(uuid4()), "date": date.today().isoformat()},
            headers=_auth(denied_token),
        )
        assert resp.status_code == 403
        # Everything else the manager role grants still works.
        assert (
            inv_client.put(
                f"/api/v1/projects/{pid}", json={"name": "Renamed by denied manager"}, headers=_auth(denied_token)
            ).status_code
            == 200
        )


# ---------------------------------------------------------------------------
# D6: the owner bypass is gone
# ---------------------------------------------------------------------------


class TestOwnerBypassRemoved:
    def test_owner_without_a_company_role_cannot_read_their_project(self, inv_client, personas, lone_owner_token):
        resp = inv_client.get(f"/api/v1/projects/{personas['orphan_project']}", headers=_auth(lone_owner_token))
        assert resp.status_code == 403

    def test_company_admin_still_reads_that_project(self, inv_client, personas, admin_token):
        resp = inv_client.get(f"/api/v1/projects/{personas['orphan_project']}", headers=_auth(admin_token))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Company-wide visibility
# ---------------------------------------------------------------------------


class TestCompanyAdminVisibility:
    def test_lists_every_project_of_the_company_and_none_of_another(
        self, inv_client, invitation_app, personas, admin_token
    ):
        resp = inv_client.get("/api/v1/projects", headers=_auth(admin_token))
        assert resp.status_code == 200
        ids = {p["id"] for p in resp.get_json()["projects"]}
        assert invitation_app._test_project_id in ids
        assert personas["orphan_project"] in ids  # same company, admin never assigned
        assert personas["other_project"] not in ids

    def test_other_company_admin_sees_only_their_own(self, inv_client, invitation_app, personas, other_admin_token):
        resp = inv_client.get("/api/v1/projects", headers=_auth(other_admin_token))
        assert resp.status_code == 200
        ids = {p["id"] for p in resp.get_json()["projects"]}
        assert ids == {personas["other_project"]}

    def test_my_permissions_come_from_the_resolver(self, inv_client, invitation_app, manager_token):
        resp = inv_client.get("/api/v1/projects", headers=_auth(manager_token))
        assert resp.status_code == 200
        row = next(p for p in resp.get_json()["projects"] if p["id"] == invitation_app._test_project_id)
        assert "project:manage_invoices" in row["my_permissions"]
        assert "project:delete" not in row["my_permissions"]
