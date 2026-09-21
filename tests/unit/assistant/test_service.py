"""Unit tests for `app.application.assistant.service.AssistantService` — full S0->dispatch
pipeline, no Flask, no HTTP. Chat storage is an in-memory fake; equipment reuses the real
SQLAlchemy inventory repositories against SQLite (same pattern as test_equipment.py) so
"equipment lookup never touches the LLM port" is proven against real DB matching, not a
canned fake result.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from app.application.assistant.equipment import EquipmentService
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import RouterDecision
from app.application.assistant.ports import Decision
from app.application.assistant.router import Router
from app.application.assistant.service import AssistantService, DefaultFeatureHandlers
from app.application.inventory.item_usecases import UpdateInventoryItemUseCase
from app.domain.entities.chat_message import ChannelRef, ChatAttachment, ChatMessage
from app.domain.entities.inventory_item import InventoryItem
from app.domain.entities.project import Project
from app.domain.entities.warehouse import Warehouse
from app.infrastructure.ai.cost import InMemoryCostLedger
from app.infrastructure.ai.rate_limit import InMemoryRateLimiter
from app.infrastructure.database.repositories.sqlalchemy_inventory_repository import (
    SqlAlchemyInventoryItemRepository,
    SqlAlchemyInventoryWarehouseRepository,
)
from tests.fakes.ai import ScriptedDecision, ScriptedVision


# ---------------------------------------------------------------------------
# Fakes shared by every test in this module
# ---------------------------------------------------------------------------


class FakeMessageRepo:
    def __init__(self) -> None:
        self.messages: dict[UUID, ChatMessage] = {}

    def add(self, message: ChatMessage) -> None:
        self.messages[message.id] = message

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        return self.messages.get(message_id)

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None:
        current = self.messages[message_id]
        self.messages[message_id] = replace(current, payload=payload)

    def list_recent_text(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        items = [m for m in self.messages.values() if m.channel == channel and m.content_type == "text"]
        items.sort(key=lambda m: m.created_at)
        return items[-limit:]


class FakeSession:
    def commit(self) -> None:
        pass


class FakeCompanyAccessRepo:
    def __init__(self, company_ids: list[UUID]) -> None:
        self._company_ids = company_ids

    def list_for_user(self, user_id: UUID) -> list[Any]:
        return [_Access(company_id=cid) for cid in self._company_ids]


class _Access:
    def __init__(self, company_id: UUID) -> None:
        self.company_id = company_id


class FakeProjectRepo:
    def __init__(self, projects: list[Project]) -> None:
        self._by_id = {p.id: p for p in projects}

    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        return self._by_id.get(project_id)

    def list_for_user_and_companies(self, user_id: UUID, company_ids: list[UUID]) -> list[Project]:
        return [p for p in self._by_id.values() if p.owner_id in company_ids]


class FakeMembership:
    def is_member(self, user_id: UUID, company_id: UUID) -> bool:
        return True


class FakeChecker:
    def has_permission(self, user_id: UUID, permission_name: str) -> bool:
        return True

    def has_permission_in_company(self, user_id: UUID, permission_name: str, company_id: UUID) -> bool:
        return True


class FakeProjectCompanyReader:
    def __init__(self, owners: dict[UUID, UUID]) -> None:
        self._owners = owners

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        return self._owners.get(project_id)


def _user_message(channel: ChannelRef, body: Optional[str] = "bonjour", photo: bool = False) -> ChatMessage:
    attachment = (
        ChatAttachment(storage_key="k", filename="p.jpg", content_type="image/jpeg", size_bytes=1) if photo else None
    )
    return ChatMessage.create(channel=channel, sender_id=uuid4(), body=body, attachment=attachment)


@pytest.fixture
def world(session):
    company_id = uuid4()
    user_id = uuid4()
    channel = ChannelRef(kind="assistant", id=user_id)
    project = Project(id=uuid4(), name="Villa Arcueil", owner_id=company_id, created_at=datetime.now(timezone.utc))

    item_repo = SqlAlchemyInventoryItemRepository(session)
    warehouse_repo = SqlAlchemyInventoryWarehouseRepository(session)
    warehouse = warehouse_repo.add(Warehouse.create(company_id=company_id, name="Entrepôt"))
    drill = item_repo.add(
        InventoryItem.create(
            company_id=company_id,
            name="Perceuse Bosch",
            quantity=1,
            condition="working",
            location_type="warehouse",
            warehouse_id=warehouse.id,
        )
    )
    session.commit()

    project_repo = FakeProjectRepo([project])
    update_item_usecase = UpdateInventoryItemUseCase(
        item_repo=item_repo,
        warehouse_repo=warehouse_repo,
        project_reader=FakeProjectCompanyReader({project.id: company_id}),
        membership_reader=FakeMembership(),
        permission_checker=FakeChecker(),
        db_session=session,
    )
    equipment = EquipmentService(
        item_repo=item_repo,
        warehouse_repo=warehouse_repo,
        project_repo=project_repo,
        update_item_usecase=update_item_usecase,
    )

    message_repo = FakeMessageRepo()
    db_session = FakeSession()
    messenger = AssistantMessenger(message_repo, db_session)
    vision = ScriptedVision(text_answers=["Réponse du chat"])
    cost_ledger = InMemoryCostLedger(daily_cap_usd=5.0)
    rate_limiter = InMemoryRateLimiter()

    def build_service(decision_port) -> AssistantService:
        return AssistantService(
            message_repo=message_repo,
            messenger=messenger,
            router=Router(decision_port),
            equipment=equipment,
            company_access_repo=FakeCompanyAccessRepo([company_id]),
            project_repo=project_repo,
            vision=vision,
            cost_ledger=cost_ledger,
            rate_limiter=rate_limiter,
        )

    return {
        "message_repo": message_repo,
        "messenger": messenger,
        "vision": vision,
        "cost_ledger": cost_ledger,
        "rate_limiter": rate_limiter,
        "channel": channel,
        "user_id": user_id,
        "company_id": company_id,
        "project": project,
        "drill": drill,
        "build_service": build_service,
    }


def _fixed_decision(
    intent: str, confidence: float = 0.95, is_write: float = 0.0, project_hint: Optional[str] = None
) -> Decision:
    hint_value = project_hint or "none"
    return Decision(
        choices={
            "intent": (intent, confidence, {intent: confidence}),
            "merchant": ("none", 0.9, {"none": 0.9}),
            "project_hint": (hint_value, 0.9, {hint_value: 0.9}),
        },
        nouls={"is_write": is_write},
    )


def _last_message(world) -> ChatMessage:
    return sorted(world["message_repo"].messages.values(), key=lambda m: m.created_at)[-1]


# ---------------------------------------------------------------------------
# find_equipment / move_equipment never touch the LLM port
# ---------------------------------------------------------------------------


def test_find_equipment_never_calls_the_vision_port(world) -> None:
    message = _user_message(world["channel"], body="Où est la perceuse ?")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("find_equipment")))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert world["vision"].json_calls == []
    assert world["vision"].text_calls == []
    reply = _last_message(world)
    assert reply.sender_id is None
    assert "Perceuse Bosch" in (reply.body or "")


def test_move_equipment_never_calls_the_vision_port(world) -> None:
    message = _user_message(world["channel"], body="Déplace la perceuse vers Villa Arcueil")
    world["message_repo"].add(message)
    decision = _fixed_decision("move_equipment", is_write=0.95, project_hint="Villa Arcueil")
    service = world["build_service"](ScriptedDecision(fixed=decision))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert world["vision"].json_calls == []
    assert world["vision"].text_calls == []
    reply = _last_message(world)
    assert "déplacé" in (reply.body or "").lower()


# ---------------------------------------------------------------------------
# Cost cap
# ---------------------------------------------------------------------------


def test_over_cost_cap_answers_quota_template_and_calls_no_provider(world) -> None:
    world["cost_ledger"].add("deepseek", 100.0)  # blow well past the 5 USD cap
    message = _user_message(world["channel"], body="Où est la perceuse ?")
    world["message_repo"].add(message)
    decision_port = ScriptedDecision(fixed=_fixed_decision("find_equipment"))
    service = world["build_service"](decision_port)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert decision_port.calls == []
    assert world["vision"].json_calls == [] and world["vision"].text_calls == []
    reply = _last_message(world)
    assert reply.body == "Le quota du jour est atteint, réessaie demain."


# ---------------------------------------------------------------------------
# Provider not configured
# ---------------------------------------------------------------------------


def test_router_not_configured_answers_the_not_configured_template(world) -> None:
    message = _user_message(world["channel"], body="Où est la perceuse ?")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(raise_not_configured=True))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert reply.body == "L'assistant n'est pas encore entièrement configuré sur ce serveur."


# ---------------------------------------------------------------------------
# Chit-chat / question -> DeepSeek text (or the free "greeting" template)
# ---------------------------------------------------------------------------


def test_trivial_greeting_skips_the_vision_port(world) -> None:
    message = _user_message(world["channel"], body="Bonjour")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("chit_chat")))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert world["vision"].text_calls == []
    reply = _last_message(world)
    assert (
        reply.body
        == "Bonjour ! Envoie-moi la photo d'un ticket ou d'un matériau, ou demande-moi où se trouve un outil."
    )


def test_non_trivial_chit_chat_calls_deepseek_text(world) -> None:
    message = _user_message(world["channel"], body="Raconte-moi une blague sur le chantier")
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("chit_chat")))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert len(world["vision"].text_calls) == 1
    reply = _last_message(world)
    assert reply.body == "Réponse du chat"


# ---------------------------------------------------------------------------
# Photo without text -> photo_ask_kind choice
# ---------------------------------------------------------------------------


def test_photo_without_text_posts_the_ask_kind_choice(world) -> None:
    message = _user_message(world["channel"], body=None, photo=True)
    world["message_repo"].add(message)
    decision_port = ScriptedDecision(fixed=_fixed_decision("chit_chat"))
    service = world["build_service"](decision_port)

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    assert decision_port.calls == []  # the router never even runs for this branch
    reply = _last_message(world)
    assert reply.content_type == "choice"
    actions = {opt["action"] for opt in reply.payload["options"]}
    assert actions == {"import_ticket", "identify_material"}


# ---------------------------------------------------------------------------
# Low-confidence intent -> clarifying choice, then handle_action("clarify_intent")
# ---------------------------------------------------------------------------


def test_low_confidence_intent_asks_a_clarifying_choice(world) -> None:
    message = _user_message(world["channel"], body="un truc chelou")
    world["message_repo"].add(message)
    low_confidence = Decision(
        choices={
            "intent": ("question", 0.4, {"question": 0.4, "chit_chat": 0.35, "find_equipment": 0.25}),
            "merchant": ("none", 0.9, {"none": 0.9}),
            "project_hint": ("none", 0.9, {"none": 0.9}),
        },
        nouls={"is_write": 0.0},
    )
    service = world["build_service"](ScriptedDecision(fixed=low_confidence))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert reply.content_type == "choice"
    intents_offered = {opt["payload"]["intent"] for opt in reply.payload["options"]}
    assert intents_offered == {"question", "chit_chat"}


def test_clarify_intent_action_redispatches_with_the_chosen_intent(world) -> None:
    original = _user_message(world["channel"], body="Où est la perceuse ?")
    world["message_repo"].add(original)
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Tu veux faire quoi ?",
        [
            {
                "label": "Trouver un outil",
                "action": "clarify_intent",
                "payload": {"message_id": str(original.id), "intent": "find_equipment"},
            }
        ],
        reply_to_id=original.id,
    )
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision("find_equipment")))

    service.handle_action(
        user_id=world["user_id"],
        message_id=choice.id,
        action="clarify_intent",
        payload={"message_id": str(original.id), "intent": "find_equipment"},
    )

    reply = _last_message(world)
    assert "Perceuse Bosch" in (reply.body or "")


# ---------------------------------------------------------------------------
# Feature stubs (identify_material/import_ticket/fetch_invoice) default to "not available yet"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("intent", ["identify_material", "import_ticket", "fetch_invoice"])
def test_unimplemented_feature_intents_answer_not_available_yet(world, intent: str) -> None:
    body = "Peu importe, fais ce que tu veux"  # unambiguously French ("fais", "que", "tu")
    message = _user_message(world["channel"], body=body)
    world["message_repo"].add(message)
    service = world["build_service"](ScriptedDecision(fixed=_fixed_decision(intent)))

    service.handle_message(user_id=world["user_id"], message_id=message.id)

    reply = _last_message(world)
    assert reply.body == "Cette fonctionnalité arrive bientôt, pas encore disponible."


def test_default_feature_handlers_matches_the_protocol_signature() -> None:
    handlers = DefaultFeatureHandlers()
    message_repo = FakeMessageRepo()
    messenger = AssistantMessenger(message_repo, FakeSession())
    channel = ChannelRef(kind="assistant", id=uuid4())
    original = _user_message(channel)
    message_repo.add(original)

    handlers.identify_material(
        user_id=channel.id, message_id=original.id, lang="fr", messenger=messenger, trace_id="t1"
    )
    handlers.import_ticket(user_id=channel.id, message_id=original.id, lang="fr", messenger=messenger, trace_id="t2")
    handlers.fetch_invoice(
        user_id=channel.id,
        message_id=original.id,
        lang="fr",
        messenger=messenger,
        trace_id="t3",
        decision=RouterDecision(intent="fetch_invoice", intent_confidence=1.0),
    )
    assert len(message_repo.messages) == 4  # original + 3 replies


# ---------------------------------------------------------------------------
# move_equipment choice flow via handle_action
# ---------------------------------------------------------------------------


def test_move_equipment_confirm_action_executes_the_move(world) -> None:
    original = _user_message(world["channel"], body="Déplace la perceuse")
    world["message_repo"].add(original)
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Déplacer Perceuse Bosch vers Villa Arcueil ?",
        [
            {
                "label": "Confirmer",
                "action": "move_equipment_confirm",
                "payload": {"item_id": str(world["drill"].id), "project_id": str(world["project"].id)},
            }
        ],
        reply_to_id=original.id,
    )
    service = world["build_service"](ScriptedDecision())

    service.handle_action(
        user_id=world["user_id"],
        message_id=choice.id,
        action="move_equipment_confirm",
        payload={"item_id": str(world["drill"].id), "project_id": str(world["project"].id)},
    )

    reply = _last_message(world)
    assert "déplacé" in (reply.body or "").lower()


def test_move_equipment_cancel_action_posts_nothing(world) -> None:
    original = _user_message(world["channel"], body="Déplace la perceuse")
    world["message_repo"].add(original)
    choice = world["messenger"].post_choice(
        world["user_id"],
        "Déplacer ?",
        [{"label": "Annuler", "action": "move_equipment_cancel", "payload": {}}],
        reply_to_id=original.id,
    )
    before = len(world["message_repo"].messages)
    service = world["build_service"](ScriptedDecision())

    service.handle_action(user_id=world["user_id"], message_id=choice.id, action="move_equipment_cancel", payload={})

    assert len(world["message_repo"].messages) == before
