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
    assert sender.sent[0].title == "Expense added"
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


def _texts(events, locale):
    return events.recorded[-1][3][locale]


def test_expense_amount_is_written_in_each_readers_format_and_titled_by_kind():
    from decimal import Decimal

    from app.domain.labor.export.format import format_eur_fr

    a, b = uuid4(), uuid4()
    notifier, _, events = _build([(a, "admin"), (b, "admin")])
    notifier.admin_acted(
        "invoice_created",
        actor_id=a,
        subject="INV-1 · ACME",
        project_id=uuid4(),
        amount=Decimal("1234.56"),
        extra_data={"invoice_id": "abc"},
    )
    fr_amount = format_eur_fr(Decimal("1234.56"))
    assert fr_amount.startswith("1") and "234,56" in fr_amount
    assert _texts(events, "fr") == [
        "Nouvelle dépense",
        f"user-{str(a)[:4]} · INV-1 · ACME · {fr_amount} · 12 rue Jean Jaurès",
    ]
    assert _texts(events, "en")[1].endswith("· €1,234.56 · 12 rue Jean Jaurès")
    assert fr_amount in _texts(events, "vi")[1]
    assert events.recorded[-1][4]["invoice_id"] == "abc"

    notifier.admin_acted(
        "invoice_created",
        actor_id=a,
        subject="INV-2",
        project_id=uuid4(),
        amount=Decimal("300"),
        variant="cash_advance",
    )
    assert _texts(events, "fr")[0] == "Avance en espèces"
    notifier.admin_acted("invoice_deleted", actor_id=a, subject="INV-3", project_id=uuid4(), variant="released_funds")
    assert _texts(events, "fr")[0] == "Déblocage de fonds supprimé"


def test_role_and_api_key_are_localized():
    a, b, target = uuid4(), uuid4(), uuid4()
    notifier, _, events = _build([(a, "admin"), (b, "admin"), (target, "member")])
    notifier.admin_acted(
        "member_role_changed",
        actor_id=a,
        subject="",
        company_id=uuid4(),
        about_user_id=target,
        role="manager",
        via_api_key="MCP",
    )
    fr = _texts(events, "fr")[1]
    assert "(clé API MCP)" in fr and fr.endswith("→ Manager")
    assert _texts(events, "vi")[1].endswith("→ Quản lý")
    assert events.recorded[-1][4]["user_id"] == str(target)
    assert events.recorded[-1][4]["actor_id"] == str(a)


def test_a_worker_stopped_company_wide_says_so():
    a, b = uuid4(), uuid4()
    notifier, _, events = _build([(a, "admin"), (b, "admin")])
    notifier.admin_acted("worker_removed", actor_id=a, subject="Nguyen", project_id=uuid4(), all_sites=True)
    assert _texts(events, "fr") == [
        "Ouvrier arrêté",
        f"user-{str(a)[:4]} · Nguyen · tous les chantiers de l'entreprise",
    ]


def test_a_code_join_is_pushed_but_not_recorded_in_the_bell():
    joiner, admin = uuid4(), uuid4()
    notifier, sender, events = _build([(admin, "admin"), (joiner, "member")])
    notifier.admin_acted("member_joined", actor_id=joiner, subject="QA Co", company_id=uuid4(), record=False)
    assert [m.token for m in sender.sent] == [f"tok-{admin}"]
    assert sender.sent[0].title == "New member"
    assert events.recorded == []
