"""Fake `app.application.assistant.ports` implementations — no network, deterministic.

Used by `tests/unit/assistant/*` and (once wired) by `tests/conftest.py`'s test app so
the assistant pipeline is fully exercisable without real DeepSeek/Jev/Gemini credentials.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

from app.application.assistant.exceptions import LlmOutputError, ProviderNotConfiguredError
from app.application.assistant.ports import ChoiceQuestion, Decision, NoulQuestion

T = TypeVar("T", bound=BaseModel)


class ScriptedVision:
    """Fake VisionLlmPort: returns pre-scripted answers, records every call.

    `json_answers`/`text_answers` are consumed in order (one call each); when exhausted
    the last one keeps being returned. Pass `fail_json_times=N` to simulate DeepSeek's
    JSON output failing validation on its first N calls (retried once for real by
    `DeepSeekVisionLlm` in production — here it simply raises `LlmOutputError`, matching
    what happens once `chat_json`'s own retry is exhausted).
    """

    def __init__(
        self,
        json_answers: Optional[list[BaseModel]] = None,
        text_answers: Optional[list[str]] = None,
        raise_not_configured: bool = False,
        raise_llm_output_error: bool = False,
    ) -> None:
        self._json_answers = list(json_answers or [])
        self._text_answers = list(text_answers or [])
        self._raise_not_configured = raise_not_configured
        self._raise_llm_output_error = raise_llm_output_error
        self.json_calls: list[tuple[str, str, int]] = []  # (system, user_text, n_images)
        self.text_calls: list[tuple[str, str]] = []

    def chat_json(
        self, system: str, user_text: str, images: list[bytes], model_cls: type[T], temperature: float = 0.0
    ) -> T:
        self.json_calls.append((system, user_text, len(images)))
        if self._raise_not_configured:
            raise ProviderNotConfiguredError("DEEPSEEK_API_KEY is not configured.")
        if self._raise_llm_output_error:
            raise LlmOutputError("Scripted failure: DeepSeek output failed validation twice.")
        if not self._json_answers:
            raise AssertionError("ScriptedVision.chat_json called with no json_answers configured.")
        answer = self._json_answers[min(len(self.json_calls) - 1, len(self._json_answers) - 1)]
        assert isinstance(answer, model_cls)
        return answer

    def chat_text(self, system: str, user_text: str) -> str:
        self.text_calls.append((system, user_text))
        if self._raise_not_configured:
            raise ProviderNotConfiguredError("DEEPSEEK_API_KEY is not configured.")
        if not self._text_answers:
            raise AssertionError("ScriptedVision.chat_text called with no text_answers configured.")
        return self._text_answers[min(len(self.text_calls) - 1, len(self._text_answers) - 1)]


def _normalize(text: str) -> str:
    ascii_d = text.replace("đ", "d").replace("Đ", "D")
    folded = unicodedata.normalize("NFKD", ascii_d.lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


#: Keyword groups standing in for what a real Jev call would infer for S0's `intent`
#: question — deliberately simple (substring match) since this is a test double, not a
#: claim about real routing accuracy.
_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "find_equipment": ("o dau", "ou est", "ou se trouve", "where is", "where's"),
    "move_equipment": (
        "chuyen",
        "di chuyen",
        "deplace",
        "deplacer",
        "move the",
        "move it",
        "move this",
    ),
    "fetch_invoice": ("tim hoa don", "cherche la facture", "va chercher", "fetch the invoice", "get the invoice"),
    "import_ticket": ("hoa don nay", "ce ticket", "ce recu", "this receipt", "this ticket"),
    "identify_material": ("vat lieu nay", "ce materiau", "this material", "what is this"),
    "question": ("bao nhieu", "combien", "how many", "how much", "?"),
}


def _guess_intent(message: str, has_photo: bool) -> tuple[str, float]:
    normalized = _normalize(message)
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return intent, 0.93
    if has_photo:
        return "identify_material", 0.9
    return "chit_chat", 0.9


class ScriptedDecision:
    """Fake DecisionPort.

    With no fixed answer given, `decide()` runs a tiny keyword oracle over
    `state["message"]`/`state["has_photo"]` to stand in for what Jev would infer — used
    by the 30-utterance router test. Pass `fixed` to always return one specific
    `Decision` instead (used by tests that only care about plumbing, not routing).

    A single pipeline run can make more than one `decide()` call with different
    question sets (feature C: `attach_to`, then `faithful`/`worst_diff`, then the S3
    `project`/`category`/`duplicate_of`/`amounts_consistent` call) — `by_question_keys`
    dispatches on the exact set of question names asked so each call gets its own
    scripted `Decision` instead of everything collapsing onto one `fixed` answer.
    """

    def __init__(
        self,
        fixed: Optional[Decision] = None,
        by_question_keys: Optional[dict[frozenset[str], Decision]] = None,
        intent_guesser: Optional[Callable[[str, bool], tuple[str, float]]] = None,
        raise_not_configured: bool = False,
    ) -> None:
        self._fixed = fixed
        self._by_question_keys = by_question_keys
        self._intent_guesser = intent_guesser or _guess_intent
        self._raise_not_configured = raise_not_configured
        self.calls: list[dict[str, Any]] = []

    def decide(self, state: dict[str, Any], questions: dict[str, ChoiceQuestion | NoulQuestion]) -> Decision:
        self.calls.append(state)
        if self._raise_not_configured:
            raise ProviderNotConfiguredError("TYPESAFE_API_KEY is not configured.")
        if self._by_question_keys is not None:
            match = self._by_question_keys.get(frozenset(questions.keys()))
            if match is not None:
                return match
        if self._fixed is not None:
            return self._fixed
        intent, confidence = self._intent_guesser(str(state.get("message", "")), bool(state.get("has_photo")))
        return Decision(
            choices={
                "intent": (intent, confidence, {intent: confidence}),
                "merchant": ("none", 0.95, {"none": 0.95}),
                "project_hint": ("none", 0.95, {"none": 0.95}),
            },
            nouls={"is_write": 0.1},
        )


class RecordingImageGen:
    """Fake ImageGenPort — records calls, returns fixed bytes."""

    def __init__(self, result: bytes = b"fake-image-bytes") -> None:
        self._result = result
        self.calls: list[tuple[bytes, str]] = []

    def generate(self, image: bytes, prompt: str) -> bytes:
        self.calls.append((image, prompt))
        return self._result


# Kept for callers that want to sanity-check a raw utterance without going through
# ScriptedDecision/Router (e.g. asserting the oracle itself before trusting a test table).
def guess_intent(message: str, has_photo: bool = False) -> str:
    return _guess_intent(message, has_photo)[0]
