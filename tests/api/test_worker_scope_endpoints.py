"""Integration tests — worker scope: a member without project:manage_labor only sees their own rows.

Three callers on one project:
  owner     — project owner + manager role           → sees everything
  linked    — member role, linked to worker "Linked"  → sees only that worker
  unlinked  — member role, no worker link            → sees nothing labor/pay related

Endpoints covered: workers, labor-entries, labor-summary, labor-monthly-summary,
rate-changes, labor exports, conflicts, invoices (list/detail), labor-payments-summary,
invoices export, tag-summary, project money fields.
"""

from __future__ import annotations


import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel, WorkerModel
from app.infrastructure.database.models.associations import user_projects

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def ws_app():
    """Fully wired app (create_app configures the DI container) with seeded roles, users, workers."""
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig

    class WsTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(WsTestConfig)
    with test_app.app_context():
        db.create_all()
        hasher = Argon2PasswordHasher()
        perms = {
            name: PermissionModel(name=name, resource=name.split(":")[0], action=name.split(":")[1])
            for name in (
                "project:read",
                "project:manage_labor",
                "project:manage_invoices",
                "project:log_own_attendance",
                "user:read",
            )
        }
        manager_role = RoleModel(name="manager", description="Manager")
        manager_role.permissions.extend(perms.values())
        member_role = RoleModel(name="member", description="Member")
        member_role.permissions.extend([perms["project:read"], perms["project:log_own_attendance"]])
        db.session.add_all([*perms.values(), manager_role, member_role])
        db.session.flush()

        def user(email, role):
            u = UserModel(email=email, password_hash=hasher.hash(PASSWORD), is_active=True)
            u.roles.append(role)
            return u

        owner = user("owner@ws-test.com", manager_role)
        linked = user("linked@ws-test.com", member_role)
        unlinked = user("unlinked@ws-test.com", member_role)
        db.session.add_all([owner, linked, unlinked])
        db.session.flush()

        project = ProjectModel(name="Chantier WS", owner_id=owner.id, budget=50000)
        db.session.add(project)
        db.session.flush()
        for u in (linked, unlinked):
            db.session.execute(
                user_projects.insert().values(user_id=u.id, project_id=project.id, role_id=member_role.id)
            )
        own = WorkerModel(project_id=project.id, name="Linked", daily_rate=100, user_id=linked.id)
        other = WorkerModel(project_id=project.id, name="Other", daily_rate=80)
        db.session.add_all([own, other])
        db.session.commit()

        test_app.config["_ids"] = {
            "project": str(project.id),
            "own": str(own.id),
            "other": str(other.id),
        }
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(ws_app):
    return ws_app.test_client()


@pytest.fixture
def ids(ws_app):
    return ws_app.config["_ids"]


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def owner_h(client):
    return _login(client, "owner@ws-test.com")


@pytest.fixture
def linked_h(client):
    return _login(client, "linked@ws-test.com")


@pytest.fixture
def unlinked_h(client):
    return _login(client, "unlinked@ws-test.com")


@pytest.fixture(scope="module")
def seeded(ws_app):
    """Two attendance days per worker and one labor payment each, created by the owner through the API."""
    client = ws_app.test_client()
    h = _login(client, "owner@ws-test.com")
    ids = ws_app.config["_ids"]
    p = ids["project"]
    for worker in ("own", "other"):
        for day in ("2026-03-02", "2026-03-03"):
            r = client.post(
                f"/api/v1/projects/{p}/labor-entries",
                json={"worker_id": ids[worker], "date": day, "shift_type": "full"},
                headers=h,
            )
            assert r.status_code == 201, r.get_json()
    invoices = {}
    for worker, amount in (("own", 150), ("other", 90)):
        r = client.post(
            f"/api/v1/projects/{p}/invoices",
            json={
                "type": "labor",
                "issue_date": "2026-03-31",
                "recipient_name": "x",
                "items": [{"description": "Labor March", "quantity": 1, "unit_price": amount, "vat_rate": 0}],
                "service_month": "2026-03-01",
                "worker_id": ids[worker],
            },
            headers=h,
        )
        assert r.status_code == 201, r.get_json()
        invoices[worker] = r.get_json()["id"]
    return invoices


# ---------------------------------------------------------------------------
# Workers / entries / summaries
# ---------------------------------------------------------------------------


def test_workers_list_is_narrowed_to_own_worker(client, ids, owner_h, linked_h, unlinked_h):
    url = f"/api/v1/projects/{ids['project']}/workers"
    assert {w["id"] for w in client.get(url, headers=owner_h).get_json()["workers"]} == {ids["own"], ids["other"]}
    body = client.get(url, headers=linked_h).get_json()
    assert [w["id"] for w in body["workers"]] == [ids["own"]] and body["total"] == 1
    assert client.get(url, headers=unlinked_h).get_json() == {"workers": [], "total": 0}


def test_entries_list_forces_own_worker_even_when_another_is_requested(
    client, ids, seeded, owner_h, linked_h, unlinked_h
):
    url = f"/api/v1/projects/{ids['project']}/labor-entries"
    assert client.get(url, headers=owner_h).get_json()["total"] == 4
    body = client.get(f"{url}?worker_id={ids['other']}", headers=linked_h).get_json()
    assert body["total"] == 2 and {e["worker_id"] for e in body["entries"]} == {ids["own"]}
    assert client.get(url, headers=unlinked_h).get_json() == {"entries": [], "total": 0}


def test_summary_rows_and_totals_only_cover_own_worker(client, ids, seeded, owner_h, linked_h, unlinked_h):
    url = f"/api/v1/projects/{ids['project']}/labor-summary?from=2026-03-01&to=2026-03-31"
    full = client.get(url, headers=owner_h).get_json()
    assert len(full["rows"]) == 2 and full["total_cost"] == 360
    own = client.get(url, headers=linked_h).get_json()
    assert [r["worker_id"] for r in own["rows"]] == [ids["own"]]
    assert own["total_days"] == 2 and own["total_cost"] == 200
    none = client.get(url, headers=unlinked_h).get_json()
    assert none["rows"] == [] and none["total_cost"] == 0


def test_monthly_summary_only_covers_own_worker(client, ids, seeded, owner_h, linked_h, unlinked_h):
    url = f"/api/v1/projects/{ids['project']}/labor-monthly-summary"
    full = client.get(url, headers=owner_h).get_json()["rows"]
    assert full and len(full[0]["workers"]) == 2
    own = client.get(url, headers=linked_h).get_json()["rows"]
    assert len(own) == 1 and [w["worker_id"] for w in own[0]["workers"]] == [ids["own"]]
    assert own[0]["total_cost"] == 200 and own[0]["total_days"] == 2
    assert client.get(url, headers=unlinked_h).get_json()["rows"] == []


def test_rate_changes_of_another_worker_are_forbidden(client, ids, linked_h):
    base = f"/api/v1/projects/{ids['project']}/workers"
    assert client.get(f"{base}/{ids['other']}/rate-changes", headers=linked_h).status_code == 403
    assert client.get(f"{base}/{ids['own']}/rate-changes", headers=linked_h).status_code == 200


def test_whole_project_views_are_forbidden_to_restricted_members(client, ids, linked_h):
    p = ids["project"]
    for url in (
        f"/api/v1/projects/{p}/labor-export?from=2026-03&to=2026-03&format=xlsx",
        f"/api/v1/projects/{p}/workers/{ids['other']}/labor-export?from=2026-03&to=2026-03&format=xlsx",
        f"/api/v1/projects/{p}/labor-entries/conflicts?date=2026-03-02&person_ids=",
        f"/api/v1/projects/{p}/invoices-export?from=2026-03&to=2026-03&format=xlsx",
        f"/api/v1/projects/{p}/tag-summary",
    ):
        r = client.get(url, headers=linked_h)
        assert r.status_code == 403 and r.get_json()["message"] == "Only project managers may view this", url


# ---------------------------------------------------------------------------
# Money: invoices, payments summary, project fields
# ---------------------------------------------------------------------------


def test_invoices_list_only_own_labor_payments_with_zeroed_aggregates(
    client, ids, seeded, owner_h, linked_h, unlinked_h
):
    url = f"/api/v1/projects/{ids['project']}/invoices"
    assert client.get(url, headers=owner_h).get_json()["total"] == 2
    body = client.get(url, headers=linked_h).get_json()
    assert [i["id"] for i in body["invoices"]] == [seeded["own"]]
    assert body["funds_released_total"] == 0 and body["company_spent_total"] == 0 and body["company_name"] is None
    assert client.get(url, headers=unlinked_h).get_json()["invoices"] == []


def test_invoice_detail_of_another_worker_is_hidden(client, ids, seeded, linked_h):
    base = f"/api/v1/projects/{ids['project']}/invoices"
    assert client.get(f"{base}/{seeded['other']}", headers=linked_h).status_code == 404
    assert client.get(f"{base}/{seeded['own']}", headers=linked_h).status_code == 200
    assert client.get(f"{base}/{seeded['other']}/attachments", headers=linked_h).status_code == 404
    assert client.get(f"{base}/{seeded['own']}/attachments", headers=linked_h).status_code == 200


def test_labor_payments_summary_only_own_worker(client, ids, seeded, owner_h, linked_h, unlinked_h):
    url = f"/api/v1/projects/{ids['project']}/labor-payments-summary"
    full = client.get(url, headers=owner_h).get_json()["months"]
    assert full and len(full[0]["workers"]) == 2 and full[0]["total_paid"] == 240
    own = client.get(url, headers=linked_h).get_json()["months"]
    assert len(own) == 1 and [w["worker_id"] for w in own[0]["workers"]] == [ids["own"]]
    assert own[0]["total_paid"] == 150
    assert client.get(url, headers=unlinked_h).get_json()["months"] == []


def test_project_money_fields_hidden_from_restricted_members(client, ids, seeded, owner_h, linked_h):
    detail = f"/api/v1/projects/{ids['project']}"
    full = client.get(detail, headers=owner_h).get_json()
    assert full["budget"] == 50000 and full["labor_accrued"] > 0
    own = client.get(detail, headers=linked_h).get_json()
    assert own["budget"] is None and own["spent"] == 0 and own["labor_accrued"] == 0
    assert "project:manage_labor" not in own["my_permissions"]
    listed = client.get("/api/v1/projects", headers=linked_h).get_json()["projects"]
    assert listed and listed[0]["budget"] is None and listed[0]["spent"] == 0
