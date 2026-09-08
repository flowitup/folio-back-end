"""Unit tests for BillingPushNotifier — refund audience and the status split."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.application.push.billing_push_notifier import BillingPushNotifier
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


@dataclass
class Named:
    name: str


class StubProjects:
    def find_by_id(self, entity_id):
        return Named("Chantier Arcueil")


@dataclass
class Access:
    user_id: object
    role: str


class StubAccess:
    def __init__(self, rows) -> None:
        self._rows = rows

    def list_for_company(self, company_id):
        return self._rows


def _build(access=None):
    sender = RecordingSender()
    d = PushDispatcher(devices=StubDevices(), sender=sender, locale="en", run_async=False)
    return BillingPushNotifier(d, StubProjects(), access), sender


# -- refunds ----------------------------------------------------------------


def test_refund_pending_reaches_the_payer_not_the_admin_who_set_it():
    notifier, sender = _build()
    payer, admin = uuid4(), uuid4()
    notifier.refund_status_changed(
        status="refund_pending", payer_id=payer, actor_id=admin, project_id=uuid4(), invoice_id=uuid4()
    )
    assert [m.token for m in sender.sent] == [f"tok-{payer}"]
    assert sender.sent[0].data["kind"] == "refund_requested"
    assert sender.sent[0].body == "Your expense is awaiting reimbursement · Chantier Arcueil"


def test_refunded_uses_the_completed_kind():
    notifier, sender = _build()
    payer = uuid4()
    notifier.refund_status_changed(
        status="refunded", payer_id=payer, actor_id=uuid4(), project_id=uuid4(), invoice_id=uuid4()
    )
    assert sender.sent[0].data["kind"] == "refund_completed"


def test_an_admin_refunding_their_own_expense_is_not_notified():
    notifier, sender = _build()
    me = uuid4()
    notifier.refund_status_changed(status="refunded", payer_id=me, actor_id=me, project_id=uuid4(), invoice_id=uuid4())
    assert sender.sent == []


def test_a_status_that_is_not_news_sends_nothing():
    notifier, sender = _build()
    notifier.refund_status_changed(
        status="refundable", payer_id=uuid4(), actor_id=uuid4(), project_id=uuid4(), invoice_id=uuid4()
    )
    assert sender.sent == []


# -- document status --------------------------------------------------------


def test_accepted_goes_to_the_author_and_the_company_admins():
    author, admin, member = uuid4(), uuid4(), uuid4()
    notifier, sender = _build(StubAccess([Access(admin, "admin"), Access(member, "member")]))
    notifier.document_status_changed(
        status="accepted",
        author_id=author,
        actor_id=uuid4(),
        document_id=uuid4(),
        company_id=uuid4(),
        number="DEV-2026-001",
    )
    tokens = sorted(m.token for m in sender.sent)
    assert tokens == sorted([f"tok-{author}", f"tok-{admin}"])
    assert sender.sent[0].body == "DEV-2026-001"


def test_overdue_stays_with_the_author():
    author, admin = uuid4(), uuid4()
    notifier, sender = _build(StubAccess([Access(admin, "admin")]))
    notifier.document_status_changed(
        status="overdue",
        author_id=author,
        actor_id=uuid4(),
        document_id=uuid4(),
        company_id=uuid4(),
        number="FAC-2026-009",
    )
    assert [m.token for m in sender.sent] == [f"tok-{author}"]


def test_rejected_stays_with_the_author():
    author, admin = uuid4(), uuid4()
    notifier, sender = _build(StubAccess([Access(admin, "admin")]))
    notifier.document_status_changed(
        status="rejected", author_id=author, actor_id=uuid4(), document_id=uuid4(), company_id=uuid4(), number="D-1"
    )
    assert [m.token for m in sender.sent] == [f"tok-{author}"]


def test_the_actor_is_excluded_even_when_they_are_an_admin_recipient():
    author, admin = uuid4(), uuid4()
    notifier, sender = _build(StubAccess([Access(admin, "admin")]))
    notifier.document_status_changed(
        status="paid", author_id=author, actor_id=admin, document_id=uuid4(), company_id=uuid4(), number="F-1"
    )
    assert [m.token for m in sender.sent] == [f"tok-{author}"]


def test_a_draft_transition_is_not_news():
    notifier, sender = _build()
    notifier.document_status_changed(
        status="draft", author_id=uuid4(), actor_id=uuid4(), document_id=uuid4(), company_id=None, number="X"
    )
    assert sender.sent == []


def test_a_broken_access_lookup_never_raises():
    class Exploding:
        def list_for_company(self, company_id):
            raise RuntimeError("db down")

    notifier, sender = _build(Exploding())
    notifier.document_status_changed(
        status="accepted", author_id=uuid4(), actor_id=uuid4(), document_id=uuid4(), company_id=uuid4(), number="X"
    )
    assert sender.sent == []
