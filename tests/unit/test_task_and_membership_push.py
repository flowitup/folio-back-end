"""Unit tests for the task and membership notifiers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

from app.application.push.dispatcher import PushDispatcher
from app.application.push.membership_push_notifier import MembershipPushNotifier
from app.application.push.task_push_notifier import TaskPushNotifier


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


@dataclass
class Status:
    value: str


@dataclass
class Task:
    id: UUID
    project_id: UUID
    title: str
    assignee_id: Optional[UUID]
    status: Status


@dataclass
class Named:
    name: str


class StubRepo:
    def __init__(self, name="Chantier Arcueil") -> None:
        self._name = name

    def find_by_id(self, entity_id):
        return Named(self._name)


def _dispatcher():
    sender = RecordingSender()
    return PushDispatcher(devices=StubDevices(), sender=sender, locale="en", run_async=False), sender


def _task(assignee):
    return Task(
        id=uuid4(), project_id=uuid4(), title="Poser les cloisons", assignee_id=assignee, status=Status("doing")
    )


# -- tasks ------------------------------------------------------------------


def test_assignment_notifies_the_assignee():
    d, sender = _dispatcher()
    notifier = TaskPushNotifier(d, StubRepo())
    assignee = uuid4()
    task = _task(assignee)
    notifier.task_assigned(task=task, actor_id=uuid4())
    assert [m.token for m in sender.sent] == [f"tok-{assignee}"]
    assert sender.sent[0].body == "Poser les cloisons · Chantier Arcueil"
    assert sender.sent[0].data["kind"] == "task_assigned"
    assert sender.sent[0].data["task_id"] == str(task.id)


def test_assigning_a_task_to_yourself_notifies_nobody():
    d, sender = _dispatcher()
    me = uuid4()
    TaskPushNotifier(d, StubRepo()).task_assigned(task=_task(me), actor_id=me)
    assert sender.sent == []


def test_unassigned_task_notifies_nobody():
    d, sender = _dispatcher()
    TaskPushNotifier(d, StubRepo()).task_assigned(task=_task(None), actor_id=uuid4())
    assert sender.sent == []


def test_move_notifies_the_assignee_with_the_new_column():
    d, sender = _dispatcher()
    assignee = uuid4()
    TaskPushNotifier(d, StubRepo()).task_moved(task=_task(assignee), actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons → doing · Chantier Arcueil"
    assert sender.sent[0].data["kind"] == "task_moved"


def test_a_broken_project_lookup_never_raises():
    class Exploding:
        def find_by_id(self, entity_id):
            raise RuntimeError("db down")

    d, sender = _dispatcher()
    TaskPushNotifier(d, Exploding()).task_assigned(task=_task(uuid4()), actor_id=uuid4())
    assert sender.sent == []


# -- membership -------------------------------------------------------------


def _membership():
    d, sender = _dispatcher()
    return MembershipPushNotifier(d, StubRepo("Chantier Arcueil"), StubRepo("Flowitup SAS")), sender


def test_project_member_added_notifies_that_user():
    notifier, sender = _membership()
    user, project = uuid4(), uuid4()
    notifier.notify("project_member_added", user_id=user, actor_id=uuid4(), entity_id=project)
    assert [m.token for m in sender.sent] == [f"tok-{user}"]
    assert sender.sent[0].data == {"kind": "project_member_added", "project_id": str(project)}
    assert sender.sent[0].body == "Chantier Arcueil"


def test_company_events_carry_the_company_id_and_name():
    notifier, sender = _membership()
    user, company = uuid4(), uuid4()
    notifier.notify("company_member_role_changed", user_id=user, actor_id=uuid4(), entity_id=company, role="admin")
    assert sender.sent[0].data == {"kind": "company_member_role_changed", "company_id": str(company)}
    assert sender.sent[0].body == "Flowitup SAS · admin"


def test_changing_your_own_access_notifies_nobody():
    notifier, sender = _membership()
    me = uuid4()
    notifier.notify("company_member_role_changed", user_id=me, actor_id=me, entity_id=uuid4(), role="admin")
    assert sender.sent == []


def test_removal_still_reaches_the_removed_user():
    notifier, sender = _membership()
    user = uuid4()
    notifier.notify("project_member_removed", user_id=user, actor_id=uuid4(), entity_id=uuid4())
    assert [m.token for m in sender.sent] == [f"tok-{user}"]


def test_an_unknown_event_is_ignored():
    notifier, sender = _membership()
    notifier.notify("something_else", user_id=uuid4(), actor_id=uuid4(), entity_id=uuid4())
    assert sender.sent == []
