"""Jev (TypeSafe AI) adapter — implements `DecisionPort` against `typesafe_sdk`.

Translates our provider-agnostic `ChoiceQuestion`/`NoulQuestion` into the SDK's own
`Choice`/`Noul`, calls `TypeSafeClient.system_one`, and reshapes the response back into
`Decision`. Every routing/gating decision in the pipeline goes through Jev — it never
generates text, only calibrated probabilities (plan section 0, "Decisions" row).
"""

from __future__ import annotations

from typing import Any, Optional

from typesafe_sdk import Choice, Noul, TypeSafeClient, TypeSafeError

from app.application.assistant.exceptions import DecisionError, ProviderNotConfiguredError
from app.application.assistant.ports import ChoiceQuestion, CostLedgerPort, Decision, NoulQuestion
from app.infrastructure.ai.cost import TYPESAFE_PER_CALL_USD


def _build_question(question: ChoiceQuestion | NoulQuestion) -> Any:
    if isinstance(question, ChoiceQuestion):
        return Choice(instructions=question.instructions, criteria=dict(question.criteria))
    return Noul(instructions=question.instructions)


class JevDecisionPort:
    """Implements DecisionPort against `typesafe_sdk.TypeSafeClient`."""

    def __init__(self, api_key: str, cost_ledger: CostLedgerPort, client: Optional[TypeSafeClient] = None) -> None:
        self._cost_ledger = cost_ledger
        self._client = client or TypeSafeClient(api_key=api_key)

    def decide(self, state: dict[str, Any], questions: dict[str, ChoiceQuestion | NoulQuestion]) -> Decision:
        built_questions = {name: _build_question(question) for name, question in questions.items()}
        try:
            response = self._client.system_one(state=state, questions=built_questions)
        except TypeSafeError as exc:
            raise DecisionError(f"Jev system_one failed: {exc}") from exc
        self._cost_ledger.add("jev", TYPESAFE_PER_CALL_USD)

        choices: dict[str, tuple[str, float, dict[str, float]]] = {}
        for name, question in questions.items():
            if isinstance(question, ChoiceQuestion):
                answer = response.choices[name]
                choices[name] = (answer.choice, answer.confidence, dict(answer.probabilities))

        nouls: dict[str, float] = {}
        for name, question in questions.items():
            if isinstance(question, NoulQuestion):
                nouls[name] = response.nouls[name].noul

        return Decision(choices=choices, nouls=nouls)


class NullDecisionPort:
    """DecisionPort stand-in when TYPESAFE_API_KEY is not configured."""

    def decide(self, state: dict[str, Any], questions: dict[str, ChoiceQuestion | NoulQuestion]) -> Decision:
        raise ProviderNotConfiguredError("TYPESAFE_API_KEY is not configured.")
