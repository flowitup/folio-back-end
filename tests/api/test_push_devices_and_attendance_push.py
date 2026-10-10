"""Integration tests — push device registry + attendance pushes.

POST/DELETE /push/devices                      register / forget an Expo token (token → one account)
worker self-log / change request               → validators' devices get a push (not the worker's)
validate / reject / change validate / refuse   → the worker's devices get a push
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.infrastructure.database.models import ProjectModel, UserModel, WorkerModel
from app.infrastructure.database.models.associations import user_projects
from tests.company_tenancy_helper import company_for_projects, seed_company_tenancy
from tests.auth_login_helper import mint_access_token

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
        c.attendance_push_notifier.sender = recorder  # notifier was built with the log sender

        def user(email):
            return UserModel(email=email, is_active=True)

        owner = user("owner@push-test.com")
        chef = user("chef@push-test.com")  # company manager (set by the tenancy helper)
        linked = user("linked@push-test.com")
        db.session.add_all([owner, chef, linked])
        db.session.flush()
        project = ProjectModel(
            name="Chantier Push", owner_id=owner.id, company_id=company_for_projects(db.session, owner.id)
        )
        db.session.add(project)
        db.session.flush()
        db.session.execute(user_projects.insert().values(user_id=linked.id, project_id=project.id))
        db.session.execute(user_projects.insert().values(user_id=chef.id, project_id=project.id))
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
        # Permissions come from the company role + project assignment (see the helper).
        # `chef` validates attendance: that is the company `manager` role now.
        seed_company_tenancy(test_app, roles={chef.id: "manager"})

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
    return {"Authorization": f"Bearer {mint_access_token(client, email)}"}


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
    # The message names the field, not just "Field required".
    for method in (client.post, client.delete):
        missing = method("/api/v1/push/devices", json={}, headers=owner_h)
        assert missing.status_code == 400
        assert missing.get_json()["message"].startswith("token: Field required")
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


def test_unregister_never_removes_another_users_device(client, owner_h, push_app):
    from app import db
    from app.infrastructure.database.models.push_device import PushDeviceOrm

    # Same 204 as for one's own token, so the answer does not say whose it is.
    assert client.delete("/api/v1/push/devices", json={"token": WORKER_TOKEN}, headers=owner_h).status_code == 204
    with push_app.app_context():
        assert db.session.query(PushDeviceOrm).filter_by(token=WORKER_TOKEN).count() == 1


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


# ---------------------------------------------------------------------------
# Notification preferences — GET/PUT, and the mute actually suppressing a push
# ---------------------------------------------------------------------------


def _prefs(client, headers):
    return client.get("/api/v1/notifications/preferences", headers=headers)


def test_preferences_default_to_everything_on_without_a_stored_row(client, linked_h):
    body = _prefs(client, linked_h).get_json()
    assert body == {
        "push_enabled": True,
        "chat": True,
        "attendance": True,
        "tasks": True,
        "membership": True,
        "billing": True,
    }


def test_preferences_partial_update_keeps_untouched_categories(client, owner_h):
    updated = client.put("/api/v1/notifications/preferences", json={"chat": False}, headers=owner_h).get_json()
    assert updated["chat"] is False
    assert updated["attendance"] is True and updated["push_enabled"] is True
    # Persisted, and a second partial update does not resurrect the first one.
    again = client.put("/api/v1/notifications/preferences", json={"tasks": False}, headers=owner_h).get_json()
    assert again["chat"] is False and again["tasks"] is False
    # Restore so later tests in this module see the default.
    client.put("/api/v1/notifications/preferences", json={"chat": True, "tasks": True}, headers=owner_h)


def test_preferences_reject_an_unknown_category(client, owner_h):
    r = client.put("/api/v1/notifications/preferences", json={"chatt": False}, headers=owner_h)
    assert r.status_code == 422


def test_preferences_require_auth(client):
    assert client.get("/api/v1/notifications/preferences").status_code == 401


def test_muting_attendance_suppresses_the_worker_push(client, ids, recorder, owner_h, linked_h):
    """The worker mutes attendance, so validating their day must reach nobody."""
    _register(client, linked_h, WORKER_TOKEN)
    day = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
    entry = client.post(
        f"/api/v1/projects/{ids['project']}/labor-entries/self",
        json={"date": day, "shift_type": "full"},
        headers=linked_h,
    ).get_json()["id"]
    client.put("/api/v1/notifications/preferences", json={"attendance": False}, headers=linked_h)
    try:
        recorder.sent.clear()
        assert (
            client.post(
                f"/api/v1/projects/{ids['project']}/labor-entries/{entry}/validate", headers=owner_h
            ).status_code
            == 200
        )
        assert [m.token for m in recorder.sent] == []
    finally:
        client.put("/api/v1/notifications/preferences", json={"attendance": True}, headers=linked_h)


def test_global_switch_mutes_every_category(client, linked_h, push_app):
    """push_enabled=false must mute categories the user never touched individually."""
    from wiring import get_container

    from app import db

    with push_app.app_context():
        linked_id = db.session.query(UserModel).filter_by(email="linked@push-test.com").one().id

    client.put("/api/v1/notifications/preferences", json={"push_enabled": False}, headers=linked_h)
    try:
        with push_app.app_context():
            repo = get_container().notification_preference_repository
            for category in ("chat", "attendance", "tasks", "membership", "billing"):
                assert repo.muted_user_ids([linked_id], category) == {linked_id}
    finally:
        client.put("/api/v1/notifications/preferences", json={"push_enabled": True}, headers=linked_h)
        with push_app.app_context():
            repo = get_container().notification_preference_repository
            assert repo.muted_user_ids([linked_id], "chat") == set()


def test_register_stores_the_app_language(client, owner_h, push_app):
    from app import db
    from app.infrastructure.database.models.push_device import PushDeviceOrm

    body = {"token": OWNER_TOKEN, "platform": "ios", "locale": "fr"}
    assert client.post("/api/v1/push/devices", json=body, headers=owner_h).status_code == 204
    bad = {**body, "locale": "xx"}
    assert client.post("/api/v1/push/devices", json=bad, headers=owner_h).status_code == 400
    with push_app.app_context():
        assert db.session.query(PushDeviceOrm).filter_by(token=OWNER_TOKEN).one().locale == "fr"


class _NoDueNotes:
    """The reminders query is Postgres-only SQL; this feed test only cares about the events."""

    def execute(self, **_kwargs):
        return []


@pytest.fixture(autouse=True)
def _no_due_notes(push_app):
    from wiring import get_container

    with push_app.app_context():
        container = get_container()
        original = container.list_due_notifications_usecase
        container.list_due_notifications_usecase = _NoDueNotes()
        yield
        container.list_due_notifications_usecase = original


def test_bell_feed_keeps_the_decisions_and_reads_in_the_viewers_language(client, linked_h, owner_h):
    feed = client.get("/api/v1/notifications?locale=fr", headers=linked_h).get_json()
    kinds = {e["kind"] for e in feed["events"]}
    assert {"validated", "change_refused", "rejected"} <= kinds
    assert feed["events_unread"] == len(feed["events"]) and feed["count"] >= feed["events_unread"]
    refused = next(e for e in feed["events"] if e["kind"] == "change_refused")
    assert refused["title"] == "Modification refusée" and not refused["read"] and refused["data"]["entry_id"]
    # Newest first; another account's feed is its own.
    assert [e["created_at"] for e in feed["events"]] == sorted((e["created_at"] for e in feed["events"]), reverse=True)
    owner_kinds = {e["kind"] for e in client.get("/api/v1/notifications", headers=owner_h).get_json()["events"]}
    assert "validated" not in owner_kinds


def test_marking_events_read_is_scoped_to_the_caller(client, linked_h, owner_h):
    feed = client.get("/api/v1/notifications", headers=linked_h).get_json()
    first = feed["events"][0]["id"]
    # Someone else's id is silently ignored.
    assert client.post("/api/v1/notifications/events/read", json={"ids": [first]}, headers=owner_h).status_code == 204
    assert client.get("/api/v1/notifications", headers=linked_h).get_json()["events_unread"] == len(feed["events"])
    assert client.post("/api/v1/notifications/events/read", json={"ids": [first]}, headers=linked_h).status_code == 204
    after = client.get("/api/v1/notifications", headers=linked_h).get_json()
    assert after["events_unread"] == len(feed["events"]) - 1
    assert client.post("/api/v1/notifications/events/read", json={"ids": ["nope"]}, headers=linked_h).status_code == 400
    assert client.post("/api/v1/notifications/events/read", headers=linked_h).status_code == 204
    assert client.get("/api/v1/notifications", headers=linked_h).get_json()["events_unread"] == 0
