"""Integration tests — a worker edits one of their past days, a manager settles the request.

PUT  /projects/<id>/labor-entries/<entry>/self             pending day → updated in place;
                                                           validated day → change request (values untouched)
POST /projects/<id>/labor-entries/<entry>/change/validate  applies the proposal (409 when none)
POST /projects/<id>/labor-entries/<entry>/change/reject    drops the proposal
GET  /notifications                                        kind=attendance_change with proposed_* for managers
POST /projects/<id>/labor-entries/self                     default backdate window is now 31 days
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel, WorkerModel
from app.infrastructure.database.models.associations import user_projects

PASSWORD = "Pass1234!"


@pytest.fixture(scope="module")
def cr_app():
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig

    class CrTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(CrTestConfig)
    with test_app.app_context():
        db.create_all()
        # The note due-reminder query is Postgres-only SQL; the bell tests here only
        # look at the attendance half, so feed it an empty note list.
        from app.application.notes.list_due_notifications_usecase import ListDueNotificationsUseCase
        from wiring import get_container

        class _NoNotes:
            def list_due_for_user(self, user_id, now, limit=100):
                return []

        get_container().list_due_notifications_usecase = ListDueNotificationsUseCase(note_query=_NoNotes())
        hasher = Argon2PasswordHasher()
        perms = {
            name: PermissionModel(name=name, resource="project", action=name.split(":")[1])
            for name in ("project:read", "project:manage_labor", "project:log_own_attendance")
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

        owner = user("owner@cr-test.com", manager_role)
        linked = user("linked@cr-test.com", member_role)
        unlinked = user("unlinked@cr-test.com", member_role)
        db.session.add_all([owner, linked, unlinked])
        db.session.flush()
        project = ProjectModel(name="Chantier CR", owner_id=owner.id)
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
        test_app.config["_ids"] = {"project": str(project.id), "own": str(own.id), "other": str(other.id)}
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(cr_app):
    return cr_app.test_client()


@pytest.fixture
def ids(cr_app):
    return cr_app.config["_ids"]


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def owner_h(client):
    return _login(client, "owner@cr-test.com")


@pytest.fixture
def linked_h(client):
    return _login(client, "linked@cr-test.com")


@pytest.fixture
def unlinked_h(client):
    return _login(client, "unlinked@cr-test.com")


def _entries_url(ids):
    return f"/api/v1/projects/{ids['project']}/labor-entries"


def _self_url(ids, entry_id):
    return f"{_entries_url(ids)}/{entry_id}/self"


def _utc_today():
    return datetime.now(timezone.utc).date()


@pytest.fixture(scope="module")
def seeded(cr_app):
    """A validated day (logged by the owner) for the linked worker and one for the other worker,
    plus a pending day the worker logged for yesterday."""
    client = cr_app.test_client()
    ids = cr_app.config["_ids"]
    owner = _login(client, "owner@cr-test.com")
    linked = _login(client, "linked@cr-test.com")
    out = {}
    for key, day in (("own_validated", "2026-03-02"), ("other_validated", "2026-03-03")):
        worker = "own" if key.startswith("own") else "other"
        r = client.post(
            _entries_url(ids), json={"worker_id": ids[worker], "date": day, "shift_type": "full"}, headers=owner
        )
        assert r.status_code == 201, r.get_json()
        out[key] = r.get_json()["id"]
    r = client.post(
        f"{_entries_url(ids)}/self",
        json={"date": (_utc_today() - timedelta(days=1)).isoformat(), "shift_type": "full"},
        headers=linked,
    )
    assert r.status_code == 201, r.get_json()
    out["own_pending"] = r.get_json()["id"]
    return out


def test_pending_day_is_edited_in_place(client, ids, seeded, linked_h):
    r = client.put(
        _self_url(ids, seeded["own_pending"]), json={"shift_type": "half", "note": "left early"}, headers=linked_h
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["status"] == "pending" and body["shift_type"] == "half" and body["note"] == "left early"
    assert body["change_pending"] is False and body["proposed_shift_type"] is None


def test_validated_day_becomes_a_change_request_without_touching_values(client, ids, seeded, linked_h, owner_h):
    r = client.put(
        _self_url(ids, seeded["own_validated"]),
        json={"shift_type": "half", "supplement_hours": 2, "note": "rain"},
        headers=linked_h,
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["status"] == "validated" and body["shift_type"] == "full" and body["supplement_hours"] == 0
    assert body["change_pending"] is True
    assert (body["proposed_shift_type"], body["proposed_supplement_hours"], body["proposed_note"]) == (
        "half",
        2,
        "rain",
    )
    assert body["change_requested_at"]

    listed = client.get(f"{_entries_url(ids)}?from=2026-03-01&to=2026-03-31", headers=linked_h).get_json()["entries"]
    row = next(e for e in listed if e["id"] == seeded["own_validated"])
    assert row["shift_type"] == "full" and row["proposed_shift_type"] == "half" and row["change_requested_at"]
    # Still priced as a full day while the request is open.
    assert row["effective_cost"] == 100

    bell = client.get("/api/v1/notifications", headers=owner_h).get_json()
    change = [p for p in bell["attendance_pending"] if p["kind"] == "attendance_change"]
    assert len(change) == 1 and change[0]["entry_id"] == seeded["own_validated"]
    assert change[0]["shift_type"] == "full" and change[0]["proposed_shift_type"] == "half"
    assert change[0]["proposed_supplement_hours"] == 2 and change[0]["worker_name"] == "Linked"
    assert any(p["kind"] == "attendance_pending" for p in bell["attendance_pending"])


def test_other_workers_day_and_unlinked_account_are_hidden(client, ids, seeded, linked_h, unlinked_h):
    assert (
        client.put(_self_url(ids, seeded["other_validated"]), json={"shift_type": "half"}, headers=linked_h).status_code
        == 404
    )
    assert (
        client.put(_self_url(ids, seeded["own_validated"]), json={"shift_type": "half"}, headers=unlinked_h).status_code
        == 404
    )


def test_empty_edit_is_rejected(client, ids, seeded, linked_h):
    r = client.put(_self_url(ids, seeded["own_pending"]), json={"supplement_hours": 0}, headers=linked_h)
    assert r.status_code == 400


def test_member_cannot_settle_a_change_and_no_request_is_409(client, ids, seeded, linked_h, owner_h):
    base = f"{_entries_url(ids)}/{seeded['other_validated']}/change"
    assert client.post(f"{base}/validate", headers=linked_h).status_code == 403
    assert client.post(f"{base}/validate", headers=owner_h).status_code == 409
    assert client.post(f"{base}/reject", headers=owner_h).status_code == 409


def test_manager_rejects_then_worker_asks_again_and_manager_applies(client, ids, seeded, linked_h, owner_h):
    entry = seeded["own_validated"]
    base = f"{_entries_url(ids)}/{entry}/change"
    # Open request from the earlier test (or re-open it).
    client.put(_self_url(ids, entry), json={"shift_type": "half", "supplement_hours": 2}, headers=linked_h)

    r = client.post(f"{base}/reject", headers=owner_h)
    assert r.status_code == 200 and r.get_json()["change_pending"] is False
    assert r.get_json()["shift_type"] == "full" and r.get_json()["supplement_hours"] == 0

    r = client.put(_self_url(ids, entry), json={"shift_type": "overtime", "note": "stayed late"}, headers=linked_h)
    assert r.status_code == 200 and r.get_json()["change_pending"] is True

    r = client.post(f"{base}/validate", headers=owner_h)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["change_pending"] is False and body["status"] == "validated"
    assert body["shift_type"] == "overtime" and body["note"] == "stayed late" and body["proposed_shift_type"] is None

    listed = client.get(f"{_entries_url(ids)}?from=2026-03-01&to=2026-03-31", headers=owner_h).get_json()["entries"]
    row = next(e for e in listed if e["id"] == entry)
    assert row["shift_type"] == "overtime" and row["change_requested_at"] is None and row["effective_cost"] == 150
    assert not [
        p
        for p in client.get("/api/v1/notifications", headers=owner_h).get_json()["attendance_pending"]
        if p["kind"] == "attendance_change"
    ]


def test_worker_can_log_a_missed_day_ten_days_back(client, ids, seeded, linked_h):
    day = (_utc_today() - timedelta(days=10)).isoformat()
    r = client.post(f"{_entries_url(ids)}/self", json={"date": day, "shift_type": "full"}, headers=linked_h)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["status"] == "pending"
    too_old = (_utc_today() - timedelta(days=40)).isoformat()
    assert (
        client.post(
            f"{_entries_url(ids)}/self", json={"date": too_old, "shift_type": "full"}, headers=linked_h
        ).status_code
        == 400
    )
