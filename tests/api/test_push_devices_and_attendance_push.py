"""Integration tests — push device registry + attendance pushes.

POST/DELETE /push/devices                      register / forget an Expo token (token → one account)
worker self-log / change request               → validators' devices get a push (not the worker's)
validate / reject / change validate / refuse   → the worker's devices get a push
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.infrastructure.database.models import PermissionModel, ProjectModel, RoleModel, UserModel, WorkerModel
from app.infrastructure.database.models.associations import user_projects

PASSWORD = "Pass1234!"
OWNER_TOKEN = "ExponentPushToken[owner-device-000000]"
WORKER_TOKEN = "ExponentPushToken[worker-device-00000]"


class RecordingPushSender:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, messages, on_invalid_token=None) -> None:
        self.sent.extend(messages)


@pytest.fixture(scope="module")
def push_app():
    from app import create_app, db
    from app.infrastructure.adapters.argon2_hasher import Argon2PasswordHasher
    from config import TestingConfig
    from wiring import get_container

    class PushTestConfig(TestingConfig):
        JWT_TOKEN_LOCATION = ["headers", "cookies"]
        RATELIMIT_ENABLED = False
        RATELIMIT_STORAGE_URI = "memory://"

    test_app = create_app(PushTestConfig)
    with test_app.app_context():
        db.create_all()
        recorder = RecordingPushSender()
        c = get_container()
        c.push_sender = recorder
        c.attendance_push_notifier._sender = recorder  # notifier was built with the log sender
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

        owner = user("owner@push-test.com", manager_role)
        chef = user("chef@push-test.com", member_role)  # manager through the membership role
        linked = user("linked@push-test.com", member_role)
        db.session.add_all([owner, chef, linked])
        db.session.flush()
        project = ProjectModel(name="Chantier Push", owner_id=owner.id)
        db.session.add(project)
        db.session.flush()
        db.session.execute(
            user_projects.insert().values(user_id=linked.id, project_id=project.id, role_id=member_role.id)
        )
        db.session.execute(
            user_projects.insert().values(user_id=chef.id, project_id=project.id, role_id=manager_role.id)
        )
        own = WorkerModel(project_id=project.id, name="Linked", daily_rate=100, user_id=linked.id)
        db.session.add(own)
        db.session.commit()
        test_app.config["_ids"] = {
            "project": str(project.id),
            "own": str(own.id),
            "owner": str(owner.id),
            "chef": str(chef.id),
        }
        test_app.config["_push"] = recorder
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(push_app):
    return push_app.test_client()


@pytest.fixture
def ids(push_app):
    return push_app.config["_ids"]


@pytest.fixture
def recorder(push_app):
    push_app.config["_push"].sent.clear()
    return push_app.config["_push"]


def _login(client, email):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.get_json()
    return {"Authorization": f"Bearer {r.get_json()['access_token']}"}


@pytest.fixture
def owner_h(client):
    return _login(client, "owner@push-test.com")


@pytest.fixture
def chef_h(client):
    return _login(client, "chef@push-test.com")


@pytest.fixture
def linked_h(client):
    return _login(client, "linked@push-test.com")


def _register(client, headers, token, platform="ios"):
    return client.post("/api/v1/push/devices", json={"token": token, "platform": platform}, headers=headers)


def test_register_validates_body_and_moves_a_token_between_accounts(client, owner_h, linked_h, push_app):
    assert _register(client, owner_h, "short", "ios").status_code == 400
    assert (
        client.post("/api/v1/push/devices", json={"token": OWNER_TOKEN, "platform": "web"}, headers=owner_h).status_code
        == 400
    )
    assert _register(client, owner_h, OWNER_TOKEN).status_code == 204
    assert _register(client, owner_h, OWNER_TOKEN).status_code == 204  # idempotent
    # The same physical device signs in as someone else: the token follows the account.
    assert _register(client, linked_h, OWNER_TOKEN, "android").status_code == 204
    from wiring import get_container

    repo = get_container().push_device_repository
    from uuid import UUID

    assert repo.tokens_for_users([UUID(push_app.config["_ids"]["owner"])]) == {}
    # Back to the owner for the remaining tests.
    assert _register(client, owner_h, OWNER_TOKEN).status_code == 204
    assert client.post("/api/v1/push/devices", json={"token": OWNER_TOKEN}).status_code == 401


def test_validators_get_a_push_when_a_worker_logs_a_day(client, ids, recorder, owner_h, chef_h, linked_h):
    assert _register(client, owner_h, OWNER_TOKEN).status_code == 204
    assert _register(client, chef_h, "ExponentPushToken[chef-device-0000000]").status_code == 204
    assert _register(client, linked_h, WORKER_TOKEN).status_code == 204
    day = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    r = client.post(
        f"/api/v1/projects/{ids['project']}/labor-entries/self",
        json={"date": day, "shift_type": "full"},
        headers=linked_h,
    )
    assert r.status_code == 201, r.get_json()
    tokens = sorted(m.token for m in recorder.sent)
    assert tokens == sorted([OWNER_TOKEN, "ExponentPushToken[chef-device-0000000]"])
    msg = recorder.sent[0]
    assert msg.data["kind"] == "submitted" and msg.data["entry_id"] == r.get_json()["id"]
    assert "Linked" in msg.body and "Chantier Push" in msg.body
    client.application.config["_entry"] = r.get_json()["id"]


def test_worker_gets_a_push_when_the_day_is_validated(client, ids, recorder, owner_h, push_app):
    entry = push_app.config["_entry"]
    r = client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/validate", headers=owner_h)
    assert r.status_code == 200
    assert [m.token for m in recorder.sent] == [WORKER_TOKEN]
    assert recorder.sent[0].data["kind"] == "validated"


def test_change_request_and_its_decision_push_both_ways(client, ids, recorder, owner_h, linked_h, push_app):
    entry = push_app.config["_entry"]
    r = client.put(
        f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/self", json={"shift_type": "half"}, headers=linked_h
    )
    assert r.status_code == 200 and r.get_json()["change_pending"] is True
    assert {m.data["kind"] for m in recorder.sent} == {"change_requested"} and WORKER_TOKEN not in {
        m.token for m in recorder.sent
    }
    recorder.sent.clear()
    r = client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/change/reject", headers=owner_h)
    assert r.status_code == 200
    assert [(m.token, m.data["kind"]) for m in recorder.sent] == [(WORKER_TOKEN, "change_refused")]


def test_rejecting_a_pending_day_still_pushes_the_worker(client, ids, recorder, owner_h, linked_h):
    day = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    entry = client.post(
        f"/api/v1/projects/{ids['project']}/labor-entries/self",
        json={"date": day, "shift_type": "full"},
        headers=linked_h,
    ).get_json()["id"]
    recorder.sent.clear()
    assert (
        client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/reject", headers=owner_h).status_code
        == 204
    )
    assert [(m.token, m.data["kind"]) for m in recorder.sent] == [(WORKER_TOKEN, "rejected")]


def test_unregister_removes_the_device(client, ids, recorder, owner_h, linked_h):
    assert client.delete("/api/v1/push/devices", json={"token": WORKER_TOKEN}, headers=linked_h).status_code == 204
    day = (datetime.now(timezone.utc).date() - timedelta(days=5)).isoformat()
    entry = client.post(
        f"/api/v1/projects/{ids['project']}/labor-entries/self",
        json={"date": day, "shift_type": "full"},
        headers=linked_h,
    ).get_json()["id"]
    recorder.sent.clear()
    client.post(f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/validate", headers=owner_h)
    assert recorder.sent == []
