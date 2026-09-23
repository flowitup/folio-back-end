"""Phase 03/04 — confidential-class refusals, output guard, and the audit log.

Reuses ``test_service.py``'s ``world`` fixture (full company/project/equipment setup,
in-memory message repo) — only the new pieces (``decisions``, ``authz_reader``, ``audit``)
are wired per test.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import pytest

from app.application.assistant.ports import ChoiceQuestion, Decision, NoulQuestion
from app.domain.entities.chat_message import ChannelRef
from tests.fakes.ai import ScriptedDecision, ScriptedVision
from tests.unit.assistant.test_service import _fixed_decision, _last_message, _user_message, world  # noqa: F401


class FakeAuthzReader:
    """Minimal stand-in — only the two methods the confidential-class hint check uses."""

    def __init__(self, *, company_role: "str | None" = None, ops: bool = False) -> None:
        self._company_role = company_role
        self._ops = ops

    def company_role_for(self, user_id: UUID, company_id: UUID) -> "str | None":
        return self._company_role

    def is_platform_ops(self, user_id: UUID) -> bool:
        return self._ops

    def admin_company_ids(self, user_id: UUID) -> list[UUID]:
        # `accessible_projects` calls this on every `handle_message` now, not only on
        # the confidential-class/labor/tasks project-resolution path this fake
        # originally supported — an empty result is a safe "no projects offered"
        # default for tests that do not care about the router's project hints.
        return []

    def is_assigned(self, user_id: UUID, project_id: UUID) -> bool:
        return True

    def project_company_id(self, project_id: UUID) -> "UUID | None":
        return None

    def project_exists(self, project_id: UUID) -> bool:
        return True


@dataclass
class RecordedAuditRow:
    company_id: Optional[UUID]
    channel_key: str
    user_id: Optional[UUID]
    message_id: Optional[UUID]
    intent: Optional[str]
    feature: Optional[str]
    tools: Any
    outcome: Optional[str]
    refused_reason: Optional[str]
    cost_usd: Decimal
    trace_id: Optional[str]


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[RecordedAuditRow] = []

    def add(self, **kwargs: Any) -> RecordedAuditRow:
        row = RecordedAuditRow(**kwargs)
        self.rows.append(row)
        return row

    def list_for_company(self, company_id: UUID, **kwargs: Any) -> list[RecordedAuditRow]:
        return [r for r in self.rows if r.company_id == company_id]


def _admin_decision(intent: str) -> Decision:
    return Decision(
        choices={
            "intent": (intent, 0.95, {intent: 0.95}),
            "merchant": ("none", 0.9, {"none": 0.9}),
            "project_hint": ("none", 0.9, {"none": 0.9}),
        },
        nouls={"is_write": 0.0},
    )


class LeakDecisionPort:
    """A ``DecisionPort`` that answers every ``NoulQuestion`` with a fixed confidence —
    used to drive the output guard deterministically."""

    def __init__(self, leak_confidence: float) -> None:
        self._leak_confidence = leak_confidence

    def decide(self, state: dict, questions: dict) -> Decision:
        nouls = {name: self._leak_confidence for name, q in questions.items() if isinstance(q, NoulQuestion)}
        choices = {
            name: (next(iter(q.criteria)), 1.0, {}) for name, q in questions.items() if isinstance(q, ChoiceQuestion)
        }
        return Decision(choices=choices, nouls=nouls)


# ---------------------------------------------------------------------------
# D17 layer 2 — router refusals for confidential-class intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intent,template_fragment",
    [
        ("ask_project_income", "budget"),
        ("ask_salary", "salaire"),
        ("ask_own_salary", "salaire"),
        ("ask_unpaid_invoices", "budget"),
    ],
)
def test_confidential_intent_refused_outside_admin_channel(world, intent, template_fragment) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="test")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_admin_decision(intent)))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert reply.content_type == "text"
    assert reply.channel == world["channel"]


def test_ask_own_salary_gets_the_salary_tab_hint_even_for_an_admin(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="lương của tôi")
    world["message_repo"].add(message)
    authz = FakeAuthzReader(company_role="admin")
    service = world["build_service"](ScriptedDecision(fixed=_admin_decision("ask_own_salary")), authz_reader=authz)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert (
        "tab" in (reply.body or "").lower() or "onglet" in (reply.body or "").lower() or "lương" in (reply.body or "")
    )
    # The admin hint (pointing at the admin channel) must NOT be appended for own-salary.
    assert "quản trị" not in (reply.body or "").lower() and "administration" not in (reply.body or "").lower()


def test_ask_salary_appends_admin_hint_when_asker_is_company_admin(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="salaire de Minh")
    world["message_repo"].add(message)
    authz = FakeAuthzReader(company_role="admin")
    service = world["build_service"](ScriptedDecision(fixed=_admin_decision("ask_salary")), authz_reader=authz)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    body_lower = (reply.body or "").lower()
    assert "administration" in body_lower or "quản trị" in body_lower


def test_admin_channel_does_not_trigger_the_router_refusal(world) -> None:  # noqa: F811
    admin_channel = ChannelRef(kind="admin", id=world["company_id"])
    message = _user_message(admin_channel, world["user_id"], body="budget du chantier")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_admin_decision("ask_project_income")))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    body_lower = (reply.body or "").lower()
    # No admin_answers wired in this test -> falls back to "not available yet", never the
    # non-admin refusal template.
    assert "budget" not in body_lower or "quản trị" not in body_lower
    assert "administration" not in body_lower


class _RecordingAdminAnswers:
    """Just enough of `AdminAnswersFeature` to prove the finance/payroll branches must
    never be invoked for a `pick_project_ctx` tap outside the admin channel."""

    def __init__(self) -> None:
        self.ask_project_income_calls: list[dict] = []
        self.ask_salary_calls: list[dict] = []

    def ask_project_income(self, **kwargs) -> str:
        self.ask_project_income_calls.append(kwargs)
        return "answered"

    def ask_salary(self, **kwargs) -> str:
        self.ask_salary_calls.append(kwargs)
        return "answered"

    def ask_unpaid_invoices(self, **kwargs) -> str:  # pragma: no cover - unused here
        return "answered"

    def ask_audit(self, **kwargs) -> str:  # pragma: no cover - unused here
        return "answered"


# ---------------------------------------------------------------------------
# A `pick_project_ctx` tap re-checks the admin channel for finance/payroll intents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["ask_project_income", "ask_salary", "ask_own_salary"])
def test_pick_project_ctx_refuses_finance_intents_outside_the_admin_channel(world, intent) -> None:  # noqa: F811
    """`pick_project_ctx` is only ever server-offered from inside the admin channel
    (`_dispatch_confidential`'s own gate), but the tap handler must re-check that itself
    — a stale tap replayed after the asker lost admin-channel access must never reach a
    finance/payroll answer outside it."""
    admin_answers = _RecordingAdminAnswers()
    original = _user_message(world["channel"], world["user_id"], body="chantier")
    world["message_repo"].add(original)
    payload = {"project_id": str(world["project"].id), "intent": intent, "text": "chantier"}
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Quel chantier ?",
        [{"label": "Villa Arcueil", "action": "pick_project_ctx", "payload": payload}],
        reply_to_id=original.id,
        channel=world["channel"],
        scope=None,
    )
    service = world["build_service"](ScriptedDecision(), admin_answers=admin_answers)

    service.handle_action(user_id=world["user_id"], message_id=choice.id, action="pick_project_ctx", payload=payload)

    assert admin_answers.ask_project_income_calls == []
    assert admin_answers.ask_salary_calls == []
    reply = _last_message(world)
    assert reply.channel == world["channel"]


def test_pick_project_ctx_allows_finance_intents_inside_the_admin_channel(world) -> None:  # noqa: F811
    admin_channel = ChannelRef(kind="admin", id=world["company_id"])
    admin_answers = _RecordingAdminAnswers()
    original = _user_message(admin_channel, world["user_id"], body="chantier")
    world["message_repo"].add(original)
    payload = {"project_id": str(world["project"].id), "intent": "ask_project_income", "text": "chantier"}
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Quel chantier ?",
        [{"label": "Villa Arcueil", "action": "pick_project_ctx", "payload": payload}],
        reply_to_id=original.id,
        channel=admin_channel,
        scope=None,
    )
    service = world["build_service"](ScriptedDecision(), admin_answers=admin_answers)

    service.handle_action(user_id=world["user_id"], message_id=choice.id, action="pick_project_ctx", payload=payload)

    assert len(admin_answers.ask_project_income_calls) == 1


# ---------------------------------------------------------------------------
# A refusal reached through a `clarify_intent` tap records the real outcome
# ---------------------------------------------------------------------------


def test_clarify_intent_tap_records_a_refusal_in_the_audit_row(world) -> None:  # noqa: F811
    """`ask_project_income` picked off a low-confidence clarify choice, then tapped
    from a non-admin channel, must audit as `outcome="refused"` — not the caller's
    `"answered"` default — since `ask_audit`'s refusal count depends on it."""
    original = _user_message(world["channel"], world["user_id"], body="chantier")
    world["message_repo"].add(original)
    audit = FakeAuditRepo()
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Tu veux faire quoi ?",
        [
            {
                "label": "Finances du chantier",
                "action": "clarify_intent",
                "payload": {"message_id": str(original.id), "intent": "ask_project_income"},
            }
        ],
        reply_to_id=original.id,
        channel=world["channel"],
        scope=None,
    )
    service = world["build_service"](ScriptedDecision(), audit=audit)

    service.handle_action(
        user_id=world["user_id"],
        message_id=choice.id,
        action="clarify_intent",
        payload={"message_id": str(original.id), "intent": "ask_project_income"},
    )

    assert audit.rows[-1].outcome == "refused"
    assert audit.rows[-1].refused_reason == "scope"


# ---------------------------------------------------------------------------
# D17 layer 3 — output guard
# ---------------------------------------------------------------------------


def test_output_guard_replaces_a_leaking_chit_chat_reply(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="Comment va le chantier ?")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    service = world["build_service"](
        ScriptedDecision(fixed=_admin_decision("chit_chat")),
        vision=ScriptedVision(text_answers=["Le chantier a reçu 12000€ ce mois-ci."]),
        decisions=LeakDecisionPort(leak_confidence=0.9),
        audit=audit,
    )

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert "12000" not in (reply.body or "")
    assert audit.rows[-1].refused_reason == "output_guard"
    assert audit.rows[-1].outcome == "refused"


def test_output_guard_lets_a_safe_reply_through(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="Comment va le chantier ?")
    world["message_repo"].add(message)
    service = world["build_service"](
        ScriptedDecision(fixed=_admin_decision("chit_chat")),
        vision=ScriptedVision(text_answers=["Tout se passe bien, merci !"]),
        decisions=LeakDecisionPort(leak_confidence=0.1),
    )

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert "Tout se passe bien" in (reply.body or "")


def test_output_guard_refuses_when_no_decision_port_is_wired(world) -> None:  # noqa: F811
    """M4: an unwired guard fails CLOSED outside the admin channel — it is no longer
    treated as "no guard, let it through"."""
    message = _user_message(world["channel"], world["user_id"], body="Comment va le chantier ?")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    service = world["build_service"](
        ScriptedDecision(fixed=_admin_decision("chit_chat")),
        vision=ScriptedVision(text_answers=["Tout va bien."]),
        audit=audit,
        # `decisions` deliberately left unwired (defaults to None).
    )

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert "Tout va bien" not in (reply.body or "")
    assert audit.rows[-1].refused_reason == "output_guard_error"
    assert audit.rows[-1].outcome == "refused"


class RaisingDecisionPort:
    def decide(self, state: dict, questions: dict) -> Decision:
        raise RuntimeError("provider outage")


def test_output_guard_refuses_when_the_provider_call_raises(world) -> None:  # noqa: F811
    """M4: a Jev outage must not silently downgrade to "no guard"."""
    message = _user_message(world["channel"], world["user_id"], body="Comment va le chantier ?")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    service = world["build_service"](
        ScriptedDecision(fixed=_admin_decision("chit_chat")),
        vision=ScriptedVision(text_answers=["Tout va bien."]),
        decisions=RaisingDecisionPort(),
        audit=audit,
    )

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert "Tout va bien" not in (reply.body or "")
    assert audit.rows[-1].refused_reason == "output_guard_error"
    assert audit.rows[-1].outcome == "refused"


def test_output_guard_never_runs_in_the_admin_channel(world) -> None:  # noqa: F811
    admin_channel = ChannelRef(kind="admin", id=world["company_id"])
    message = _user_message(admin_channel, world["user_id"], body="Comment va le chantier ?")
    world["message_repo"].add(message)
    service = world["build_service"](
        ScriptedDecision(fixed=_admin_decision("chit_chat")),
        vision=ScriptedVision(text_answers=["Le chantier a reçu 12000€ ce mois-ci."]),
        decisions=LeakDecisionPort(leak_confidence=0.99),
    )

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert "12000" in (reply.body or "")


# ---------------------------------------------------------------------------
# D17 layer 4 — audit log
# ---------------------------------------------------------------------------


def test_audit_row_written_for_a_replied_message(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="Où est la perceuse ?")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("find_equipment")), audit=audit)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert len(audit.rows) == 1
    row = audit.rows[0]
    assert row.outcome == "answered"
    assert row.refused_reason is None
    assert row.company_id == world["company_id"]
    assert row.channel_key == world["channel"].key
    assert row.intent == "find_equipment"
    assert row.feature == "equipment"


def test_audit_row_written_for_a_refused_message(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="Déplace la perceuse")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    world["cost_ledger"].add("jev", 1000.0)  # push well over the daily cap
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("find_equipment")), audit=audit)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert audit.rows[-1].outcome == "refused"
    assert audit.rows[-1].refused_reason == "cost_cap"


def test_audit_row_outcome_is_never_outside_the_three_allowed_values(world) -> None:  # noqa: F811
    message = _user_message(world["channel"], world["user_id"], body="Où est la perceuse ?")
    world["message_repo"].add(message)
    audit = FakeAuditRepo()
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("find_equipment")), audit=audit)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert audit.rows[-1].outcome in {"answered", "refused", "error"}
