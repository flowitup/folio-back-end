"""Unit tests for `app.application.assistant.router.Router` (S0) — no network.

`ScriptedDecision` (tests/fakes/ai.py) stands in for Jev with a tiny keyword oracle; the
30-utterance table below (10 vi/fr/en, all 7 intents represented per language) checks
that `Router.route()` correctly threads a chat message through `DecisionPort.decide()`
and reshapes its answer into `RouterDecision` — not that the oracle has real NLU
accuracy (it does not, and never claims to).
"""

from __future__ import annotations

import pytest

from app.application.assistant.models import MERCHANTS, RouterDecision
from app.application.assistant.ports import ChoiceQuestion, Decision, NoulQuestion
from app.application.assistant.router import Router
from tests.fakes.ai import ScriptedDecision

# ---------------------------------------------------------------------------
# 30-utterance routing table (10 per language, every one of the 7 intents hit)
# ---------------------------------------------------------------------------

_FRENCH = [
    ("Où est la perceuse ?", "find_equipment"),
    ("Déplace la perceuse vers Arcueil", "move_equipment"),
    ("Va chercher la facture Leroy Merlin", "fetch_invoice"),
    ("Enregistre ce ticket", "import_ticket"),
    ("Identifie ce matériau", "identify_material"),
    ("Combien coûte le ciment ?", "question"),
    ("Bonjour, merci pour votre aide.", "chit_chat"),
    ("Perceuse ou se trouve-t-elle ?", "find_equipment"),
    ("Peux-tu déplacer le carrelette au chantier Meaux ?", "move_equipment"),
    ("Cherche la facture Point P", "fetch_invoice"),
]

_VIETNAMESE = [
    ("Máy cắt gạch ở đâu?", "find_equipment"),
    ("Di chuyển máy khoan tới công trình Meaux", "move_equipment"),
    ("Tìm hoá đơn Leroy Merlin giúp tôi", "fetch_invoice"),
    ("Nhập hoá đơn này vào hệ thống", "import_ticket"),
    ("Vật liệu này là gì vậy?", "identify_material"),
    ("Ciment này giá bao nhiêu?", "question"),
    ("Chào bạn, cảm ơn nhiều nhé", "chit_chat"),
    ("Máy khoan này ở đâu vậy ta?", "find_equipment"),
    ("Chuyển giúp tôi cái thang qua công trình Arcueil", "move_equipment"),
    ("Tìm hoá đơn Point P giúp tôi", "fetch_invoice"),
]

_ENGLISH = [
    ("Where is the drill?", "find_equipment"),
    ("Move the wheelbarrow to the Meaux site", "move_equipment"),
    ("Fetch the invoice from Leroy Merlin", "fetch_invoice"),
    ("Import this receipt into the system", "import_ticket"),
    ("What is this material, can you check?", "identify_material"),
    ("How much does the cement cost?", "question"),
    ("Hello, thanks a lot for your help.", "chit_chat"),
    ("Where's the ladder?", "find_equipment"),
    ("Please move it to the Arcueil site", "move_equipment"),
    ("Get the invoice from Point P", "fetch_invoice"),
]

ROUTING_TABLE = [(text, intent) for text, intent in (*_FRENCH, *_VIETNAMESE, *_ENGLISH)]


def test_routing_table_has_30_utterances_covering_all_seven_intents() -> None:
    assert len(ROUTING_TABLE) == 30
    for batch in (_FRENCH, _VIETNAMESE, _ENGLISH):
        assert len(batch) == 10
        expected_intents = {
            "identify_material",
            "import_ticket",
            "fetch_invoice",
            "find_equipment",
            "move_equipment",
            "question",
            "chit_chat",
        }
        assert {intent for _text, intent in batch} == expected_intents


@pytest.mark.parametrize("message,expected_intent", ROUTING_TABLE)
def test_router_routes_the_utterance_to_the_expected_intent(message: str, expected_intent: str) -> None:
    router = Router(ScriptedDecision())
    decision = router.route(message, has_photo=False, project_names=[], history_texts=[])
    assert decision.intent == expected_intent


def test_router_routes_at_least_27_of_30_utterances_correctly() -> None:
    """Mirrors the plan's own M3 acceptance bar (>= 27/30) as a single regression gate."""
    router = Router(ScriptedDecision())
    correct = sum(
        1
        for message, expected_intent in ROUTING_TABLE
        if router.route(message, has_photo=False, project_names=[], history_texts=[]).intent == expected_intent
    )
    assert correct >= 27


# ---------------------------------------------------------------------------
# Plumbing: state building, merchant/project_hint reshaping, "none" -> None.
# ---------------------------------------------------------------------------


def test_route_passes_message_has_photo_projects_and_history_into_state() -> None:
    captured: dict = {}

    class CapturingDecision:
        def decide(self, state, questions):
            captured["state"] = state
            captured["questions"] = questions
            return Decision(
                choices={
                    "intent": ("chit_chat", 0.9, {"chit_chat": 0.9}),
                    "merchant": ("none", 0.9, {"none": 0.9}),
                    "project_hint": ("none", 0.9, {"none": 0.9}),
                },
                nouls={"is_write": 0.0},
            )

    router = Router(CapturingDecision())
    router.route(
        "et celle de Point P ?",
        has_photo=True,
        project_names=["Villa Arcueil", "Extension Meaux"],
        history_texts=["bonjour", "facture Leroy Merlin 79,54 €"],
    )
    assert captured["state"] == {
        "message": "et celle de Point P ?",
        "has_photo": True,
        "projects": ["Villa Arcueil", "Extension Meaux"],
        "history": ["bonjour", "facture Leroy Merlin 79,54 €"],
    }
    project_hint_question = captured["questions"]["project_hint"]
    assert isinstance(project_hint_question, ChoiceQuestion)
    assert project_hint_question.criteria["Villa Arcueil"] == "Villa Arcueil"
    assert project_hint_question.criteria["Extension Meaux"] == "Extension Meaux"
    assert project_hint_question.criteria["none"] == "aucun"
    intent_question = captured["questions"]["intent"]
    assert isinstance(intent_question, ChoiceQuestion)
    is_write_question = captured["questions"]["is_write"]
    assert isinstance(is_write_question, NoulQuestion)
    merchant_question = captured["questions"]["merchant"]
    assert set(merchant_question.criteria) == {"none", *MERCHANTS}


def test_route_maps_none_merchant_and_project_hint_to_python_none() -> None:
    fixed = Decision(
        choices={
            "intent": ("find_equipment", 0.95, {"find_equipment": 0.95}),
            "merchant": ("none", 0.9, {"none": 0.9}),
            "project_hint": ("none", 0.9, {"none": 0.9}),
        },
        nouls={"is_write": 0.05},
    )
    router = Router(ScriptedDecision(fixed=fixed))
    decision = router.route("où est la perceuse", has_photo=False, project_names=[], history_texts=[])
    assert isinstance(decision, RouterDecision)
    assert decision.merchant is None
    assert decision.project_hint is None
    assert decision.is_write == 0.05


def test_route_keeps_a_real_merchant_and_project_hint() -> None:
    fixed = Decision(
        choices={
            "intent": ("fetch_invoice", 0.9, {"fetch_invoice": 0.9}),
            "merchant": ("leroymerlin", 0.88, {"leroymerlin": 0.88}),
            "project_hint": ("Villa Arcueil", 0.7, {"Villa Arcueil": 0.7}),
        },
        nouls={"is_write": 0.6},
    )
    router = Router(ScriptedDecision(fixed=fixed))
    decision = router.route(
        "va chercher la facture Leroy Merlin pour Arcueil",
        has_photo=False,
        project_names=["Villa Arcueil"],
        history_texts=[],
    )
    assert decision.merchant == "leroymerlin"
    assert decision.project_hint == "Villa Arcueil"
    assert decision.project_hint_confidence == 0.7
