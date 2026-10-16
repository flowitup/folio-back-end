"""AttendancePushNotifier: what the validators' bell keeps, and how the worker is named."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from app.application.push.attendance_push_notifier import AttendancePushNotifier


class Sender:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, messages, on_invalid_token=None):
        self.sent.extend(messages)


class Devices:
    def __init__(self, validators) -> None:
        self._validators = validators

    def tokens_for_users(self, user_ids):
        return {u: [f"tok-{u}"] for u in user_ids}

    def validator_user_ids(self, project_id):
        return list(self._validators)

    def delete_token(self, token):
        pass


class Events:
    def __init__(self) -> None:
        self.recorded: list = []

    def record(self, user_ids, *, category, kind, texts, data):
        self.recorded.append((set(user_ids), kind, texts, data))


@dataclass
class Worker:
    id: UUID
    project_id: UUID
    user_id: UUID | None
    name: str
    person_name: str | None


class Workers:
    def __init__(self, worker) -> None:
        self._worker = worker

    def find_by_id(self, worker_id):
        return self._worker


@dataclass
class Project:
    name: str = "Villa"
    address: str = "8 avenue Foch"
    company_id: UUID | None = None


class Projects:
    def find_by_id(self, project_id):
        return Project()


class Names:
    def __init__(self, names) -> None:
        self._names = names

    def display_names(self, user_ids):
        return {u: self._names[u] for u in user_ids if u in self._names}


def _build(worker, validators, names=None):
    sender, events = Sender(), Events()
    notifier = AttendancePushNotifier(
        devices=Devices(validators),
        sender=sender,
        worker_repo=Workers(worker),
        project_repo=Projects(),
        locale="fr",
        run_async=False,
        events=events,
        names=names,
    )
    return notifier, sender, events


def test_a_submitted_day_is_pushed_but_left_to_the_to_validate_list():
    manager, worker_user = uuid4(), uuid4()
    worker = Worker(uuid4(), uuid4(), worker_user, "Bích", "Bích")
    notifier, sender, events = _build(worker, [manager])
    notifier.worker_submitted(worker.id, date(2026, 10, 15), uuid4())
    notifier.worker_submitted(worker.id, date(2026, 10, 16), uuid4(), change=True)
    assert [m.title for m in sender.sent] == ["Pointage à valider", "Demande de modification"]
    assert events.recorded == []


def test_a_decision_still_lands_in_the_workers_bell():
    worker_user = uuid4()
    worker = Worker(uuid4(), uuid4(), worker_user, "Bích", "Bích")
    notifier, _, events = _build(worker, [])
    notifier.decision(worker.id, date(2026, 10, 15), uuid4(), "validated")
    assert events.recorded[0][1] == "validated"
    assert events.recorded[0][3]["date"] == "2026-10-15"


def test_a_worker_added_by_phone_is_named_not_numbered():
    manager, worker_user = uuid4(), uuid4()
    # Directory name is the phone; the worker row has the real name.
    worker = Worker(uuid4(), uuid4(), worker_user, "Trần Thị Bích", "+33621400004")
    notifier, sender, _ = _build(worker, [manager])
    notifier.worker_submitted(worker.id, date(2026, 10, 15), uuid4())
    assert sender.sent[0].body.startswith("Trần Thị Bích a pointé le 15/10")

    # Both are the phone: the account's own name is used.
    worker = Worker(uuid4(), uuid4(), worker_user, "+33621000003", "+33621000003")
    notifier, sender, _ = _build(worker, [manager], Names({worker_user: "Châu Trần"}))
    notifier.worker_submitted(worker.id, date(2026, 10, 15), uuid4())
    assert sender.sent[0].body.startswith("Châu Trần a pointé")
