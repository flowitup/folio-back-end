"""Unit tests for CompanyActivityNotifier: who is told, and who is not."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.application.push.company_activity_notifier import CompanyActivityNotifier
from app.application.push.dispatcher import PushDispatcher


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, messages, on_invalid_token=None):
        self.sent.extend(messages)


class StubDevices:
    def tokens_for_users(self, user_ids):
        return {u: [f"tok-{u}"] for u in user_ids}

    def delete_token(self, token):
        pass


class RecordingEvents:
    def __init__(self) -> None:
        self.recorded: list = []

    def record(self, user_ids, *, category, kind, texts, data):
        self.recorded.append((set(user_ids), category, kind, texts, data))


@dataclass
class Access:
    user_id: UUID
    role: str


@dataclass
class Project:
    company_id: UUID | None
    name: str = "Chantier"
    address: str = "12 rue Jean Jaurès"


class Projects:
    def __init__(self, project) -> None:
        self._project = project

    def find_by_id(self, project_id):
        return self._project


class Access_:
    def __init__(self, rows) -> None:
        self._rows = rows

    def list_for_company(self, company_id):
        return self._rows


class Names:
    def display_names(self, user_ids):
        return {u: f"user-{str(u)[:4]}" for u in user_ids}


def _build(roles, project=None):
    sender, events = RecordingSender(), RecordingEvents()
    dispatcher = PushDispatcher(devices=StubDevices(), sender=sender, locale="en", run_async=False, events=events)
    notifier = CompanyActivityNotifier(
        dispatcher, Projects(project or Project(uuid4())), Access_([Access(u, r) for u, r in roles]), Names()
    )
    return notifier, sender, events


def test_every_other_admin_is_told_and_the_actor_is_not():
    a, b, c, manager = uuid4(), uuid4(), uuid4(), uuid4()
    notifier, sender, events = _build([(a, "admin"), (b, "admin"), (c, "admin"), (manager, "manager")])
    notifier.admin_acted("invoice_created", actor_id=a, subject="F-12 · ACME · 120.00 €", project_id=uuid4())
    assert sorted(m.token for m in sender.sent) == sorted([f"tok-{b}", f"tok-{c}"])
    assert sender.sent[0].title == "Invoice added"
    assert sender.sent[0].body.endswith("F-12 · ACME · 120.00 € · 12 rue Jean Jaurès")
    assert events.recorded[0][0] == {b, c} and events.recorded[0][1] == "billing"
    assert events.recorded[0][4]["kind"] == "invoice_created"


def test_a_sole_admin_acting_alone_tells_nobody():
    a = uuid4()
    notifier, sender, events = _build([(a, "admin")])
    notifier.admin_acted("worker_added", actor_id=a, subject="Nguyen", project_id=uuid4())
    assert sender.sent == [] and events.recorded == []


def test_the_person_an_action_is_about_is_left_to_their_own_notification():
    actor, target, other = uuid4(), uuid4(), uuid4()
    notifier, sender, _ = _build([(actor, "admin"), (target, "admin"), (other, "admin")])
    notifier.admin_acted("member_removed", actor_id=actor, subject="", company_id=uuid4(), about_user_id=target)
    assert [m.token for m in sender.sent] == [f"tok-{other}"]
    assert "user-" + str(target)[:4] in sender.sent[0].body


def test_unknown_event_or_missing_company_is_a_silent_no_op():
    a, b = uuid4(), uuid4()
    notifier, sender, _ = _build([(a, "admin"), (b, "admin")], project=Project(company_id=None))
    notifier.admin_acted("invoice_created", actor_id=a, subject="x", project_id=uuid4())
    notifier.admin_acted("no_such_event", actor_id=a, subject="x", company_id=uuid4())
    assert sender.sent == []


def test_an_action_made_with_an_api_key_names_the_key_in_the_actor():
    a, b = uuid4(), uuid4()
    notifier, sender, _ = _build([(a, "admin"), (b, "admin")])
    notifier.admin_acted("invoice_created", actor_id=a, subject="F-12", project_id=uuid4(), via_api_key="Claude plugin")
    assert sender.sent[0].body.startswith(f"user-{str(a)[:4]} (API key Claude plugin) · F-12")
