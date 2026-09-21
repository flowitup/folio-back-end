"""S0 — Router: one Jev call turns a chat message into intent/merchant/project/write-flag.

Everything downstream (`AssistantService.handle_message`) branches on `RouterDecision`;
this module only builds the Jev question set and reshapes the answer. See plan section 4
and `gate.INTENT_AUTO`/`gate.IS_WRITE` for what the caller does with the confidences.
"""

from __future__ import annotations

from app.application.assistant.models import INTENTS, MERCHANTS, RouterDecision
from app.application.assistant.ports import ChoiceQuestion, DecisionPort, NoulQuestion

_INTENT_CRITERIA: dict[str, str | None] = {
    "identify_material": "photo d'un matériau, trouver la référence",
    "import_ticket": "photo d'un ticket/facture à enregistrer",
    "fetch_invoice": "aller chercher une facture sur le site d'un fournisseur",
    "find_equipment": "où se trouve un outil",
    "move_equipment": "déclarer un déplacement d'outil",
    "question": "autre question chantier",
    "chit_chat": "autre",
}
if set(_INTENT_CRITERIA) != set(INTENTS):
    raise RuntimeError("_INTENT_CRITERIA and INTENTS have drifted apart.")

_MERCHANT_LABELS: dict[str, str] = {
    "leroymerlin": "Leroy Merlin",
    "pointp": "Point P",
    "castorama": "Castorama",
    "bricodepot": "Brico Dépôt",
    "gedimat": "Gedimat",
    "technomat": "Technomat",
    "manomano": "ManoMano",
}
if set(_MERCHANT_LABELS) != set(MERCHANTS):
    raise RuntimeError("_MERCHANT_LABELS and MERCHANTS have drifted apart.")

_NONE = "none"


class Router:
    """S0: Jev decides intent/merchant/project_hint/is_write from one chat message."""

    def __init__(self, decisions: DecisionPort) -> None:
        self._decisions = decisions

    def route(
        self,
        message_text: str,
        has_photo: bool,
        project_names: list[str],
        history_texts: list[str],
    ) -> RouterDecision:
        state = {
            "message": message_text,
            "has_photo": has_photo,
            "projects": project_names,
            "history": history_texts,
        }
        questions: dict[str, ChoiceQuestion | NoulQuestion] = {
            "intent": ChoiceQuestion(
                instructions="Nature de la demande de l'utilisateur dans le chat chantier.",
                criteria=dict(_INTENT_CRITERIA),
            ),
            "merchant": ChoiceQuestion(
                instructions="Fournisseur mentionné, sinon 'none'.",
                criteria={_NONE: "aucun", **_MERCHANT_LABELS},
            ),
            "project_hint": ChoiceQuestion(
                instructions="Chantier mentionné ou sous-entendu, sinon 'none'.",
                criteria={_NONE: "aucun", **{name: name for name in project_names}},
            ),
            "is_write": NoulQuestion(instructions="La demande modifie des données (déplacement, suppression, ...)."),
        }
        decision = self._decisions.decide(state, questions)
        intent, intent_confidence, intent_probabilities = decision.choice("intent")
        merchant, merchant_confidence, _merchant_probabilities = decision.choice("merchant")
        project_hint, project_hint_confidence, _project_probabilities = decision.choice("project_hint")
        is_write = decision.noul("is_write")
        return RouterDecision(
            intent=intent,
            intent_confidence=intent_confidence,
            intent_probabilities=intent_probabilities,
            merchant=merchant if merchant != _NONE else None,
            merchant_confidence=merchant_confidence,
            project_hint=project_hint if project_hint != _NONE else None,
            project_hint_confidence=project_hint_confidence,
            is_write=is_write,
        )
