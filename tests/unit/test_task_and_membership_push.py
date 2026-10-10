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
    task = _task(assignee)
    task.status = Status("in_progress")
    TaskPushNotifier(d, StubRepo()).task_moved(task=task, actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons → In progress · Chantier Arcueil"
    assert sender.sent[0].data["kind"] == "task_moved"


def test_move_names_the_column_in_the_dispatcher_language():
    sender = RecordingSender()
    d = PushDispatcher(devices=StubDevices(), sender=sender, locale="fr", run_async=False)
    task = _task(uuid4())
    task.status = Status("done")
    TaskPushNotifier(d, StubRepo()).task_moved(task=task, actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons → Terminé · Chantier Arcueil"


def test_move_to_the_backlog_names_it_as_the_vietnamese_board_does():
    sender = RecordingSender()
    d = PushDispatcher(devices=StubDevices(), sender=sender, locale="vi", run_async=False)
    task = _task(uuid4())
    task.status = Status("backlog")
    TaskPushNotifier(d, StubRepo()).task_moved(task=task, actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons → Việc tồn đọng · Chantier Arcueil"


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
    assert sender.sent[0].body == "Flowitup SAS · Admin"


def test_role_change_names_the_role_in_the_dispatcher_language():
    cases = (
        ("vi", "admin", "Quản trị viên"),
        ("vi", "manager", "Quản lý"),
        ("vi", "member", "Thành viên"),
        ("fr", "manager", "Responsable"),
        ("fr", "member", "Membre"),
        ("en", "manager", "Manager"),
    )
    for locale, role, expected in cases:
        sender = RecordingSender()
        d = PushDispatcher(devices=StubDevices(), sender=sender, locale=locale, run_async=False)
        notifier = MembershipPushNotifier(d, StubRepo(), StubRepo("Flowitup SAS"))
        notifier.notify("company_member_role_changed", user_id=uuid4(), actor_id=uuid4(), entity_id=uuid4(), role=role)
        assert sender.sent[0].body == f"Flowitup SAS · {expected}"


def test_role_change_keeps_an_unknown_role_as_is():
    notifier, sender = _membership()
    notifier.notify("company_member_role_changed", user_id=uuid4(), actor_id=uuid4(), entity_id=uuid4(), role="owner")
    assert sender.sent[0].body == "Flowitup SAS · owner"


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


# -- tasks: the assignee must still be able to open the project ---------------


class StubAuthz:
    """Resolver reader: one company, `assigned` users are members on every project."""

    def __init__(self, assigned=()) -> None:
        self._assigned = set(assigned)
        self._company = uuid4()

    def project_company_id(self, project_id):
        return self._company

    def company_role_for(self, user_id, company_id):
        return "member" if user_id in self._assigned else None

    def is_assigned(self, user_id, project_id):
        return user_id in self._assigned

    def grants_for(self, user_id, company_id, project_id):
        return []


class StubUsers:
    def __init__(self, inactive=()) -> None:
        self._inactive = set(inactive)

    def is_sign_in_allowed(self, user_id):
        return user_id not in self._inactive

    def find_by_id(self, user_id):
        return object()


def test_an_assignee_removed_from_the_project_is_not_told_about_the_move():
    d, sender = _dispatcher()
    notifier = TaskPushNotifier(d, StubRepo())
    notifier.set_access_reader(StubAuthz(assigned=()), StubUsers())
    notifier.task_moved(task=_task(uuid4()), actor_id=uuid4())
    assert sender.sent == []


def test_a_deactivated_assignee_is_not_told_about_the_move():
    d, sender = _dispatcher()
    assignee = uuid4()
    notifier = TaskPushNotifier(d, StubRepo(), authz_reader=StubAuthz(assigned=[assignee]))
    notifier.set_access_reader(StubAuthz(assigned=[assignee]), StubUsers(inactive=[assignee]))
    notifier.task_moved(task=_task(assignee), actor_id=uuid4())
    assert sender.sent == []


def test_an_assignee_still_on_the_project_is_told_about_the_move():
    d, sender = _dispatcher()
    assignee = uuid4()
    notifier = TaskPushNotifier(d, StubRepo())
    notifier.set_access_reader(StubAuthz(assigned=[assignee]), StubUsers())
    notifier.task_moved(task=_task(assignee), actor_id=uuid4())
    assert [m.token for m in sender.sent] == [f"tok-{assignee}"]


# -- project label: the site address the apps show, else the name -------------


@dataclass
class Located:
    name: str
    address: Optional[str]


class LocatedRepo:
    def __init__(self, address: Optional[str]) -> None:
        self._address = address

    def find_by_id(self, entity_id):
        return Located("Chantier Arcueil", self._address)


def test_task_push_names_the_project_by_its_address():
    d, sender = _dispatcher()
    TaskPushNotifier(d, LocatedRepo(" 3 rue QA ")).task_assigned(task=_task(uuid4()), actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons · 3 rue QA"


def test_task_push_falls_back_to_the_name_without_an_address():
    d, sender = _dispatcher()
    TaskPushNotifier(d, LocatedRepo("  ")).task_assigned(task=_task(uuid4()), actor_id=uuid4())
    assert sender.sent[0].body == "Poser les cloisons · Chantier Arcueil"


def test_project_membership_push_names_the_project_by_its_address():
    d, sender = _dispatcher()
    notifier = MembershipPushNotifier(d, LocatedRepo("3 rue QA"), StubRepo("Flowitup SAS"))
    notifier.notify("project_member_added", user_id=uuid4(), actor_id=uuid4(), entity_id=uuid4())
    assert sender.sent[0].body == "3 rue QA"
