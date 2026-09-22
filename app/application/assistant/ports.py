"""Ports (Protocols) of the assistant application layer.

Everything below `MessagePosterPort` is phase 02: the provider-facing contracts every
`app.infrastructure.ai` adapter implements, plus the small question/answer DTOs that
let `router.py` describe a Jev call without importing `typesafe_sdk` from the
application layer (only the infrastructure adapter — `jev_client.py` — touches the SDK).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.domain.entities.chat_message import ChannelRef, ChatMessage

T = TypeVar("T", bound=BaseModel)


class AssistantDispatcherPort(Protocol):
    """Post-commit hand-off to the assistant pipeline, run out of band.

    The production adapter enqueues an RQ job; tests use an in-memory recorder. A
    failure here must never surface to the caller — sending a chat message (or
    accepting an action) has already committed by the time this runs.
    """

    def message_received(self, *, user_id: UUID, message_id: UUID) -> None:
        """A user sent a message in their assistant conversation."""
        ...

    def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
        """A user tapped a choice option (``POST /api/v1/assistant/actions``)."""
        ...


class MessagePosterPort(Protocol):
    """What ``AssistantMessenger``/``AssistantService`` need from the chat repository."""

    def add(self, message: ChatMessage) -> None: ...

    def find_by_id(self, message_id: UUID) -> ChatMessage | None: ...

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None: ...

    def answer_choice_if_unanswered(self, message_id: UUID, answered: str, answered_payload: dict[str, Any]) -> bool:
        """Atomically set ``payload["answered"]``/``["answered_payload"]`` — a single
        conditional UPDATE that only writes when the message is not already answered.
        Returns False (no write happened) when it is, so two concurrent submissions of
        the same choice can never both dispatch."""
        ...

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        """Last ``limit`` messages of a channel the assistant was addressed by/as, oldest
        first (S0 chat history) — never other chat in the channel (D18)."""
        ...


class ProjectCompanyReaderPort(Protocol):
    """The company that owns a project — resolves ``ChannelScope.company_id`` for a
    ``project:<id>`` channel."""

    def project_company_id(self, project_id: UUID) -> Optional[UUID]:
        """``None`` when the project does not exist or has no company yet."""
        ...


# ---------------------------------------------------------------------------
# Jev (TypeSafe) question/answer DTOs — provider-agnostic so router.py never
# imports typesafe_sdk directly. jev_client.py translates these to the SDK's own
# Choice/Noul before calling system_one(), and translates the response back.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceQuestion:
    """Mirrors ``typesafe_sdk.Choice``: pick one label out of ``criteria``."""

    instructions: str
    criteria: dict[str, str | None]


@dataclass(frozen=True)
class NoulQuestion:
    """Mirrors ``typesafe_sdk.Noul``: a calibrated 0..1 probability, no labels."""

    instructions: str


@dataclass(frozen=True)
class Decision:
    """Jev's answer to every question of one ``system_one`` call."""

    choices: dict[str, tuple[str, float, dict[str, float]]] = field(default_factory=dict)
    nouls: dict[str, float] = field(default_factory=dict)

    def choice(self, name: str) -> tuple[str, float, dict[str, float]]:
        """(label, confidence, probabilities) for a ``ChoiceQuestion`` answer."""
        return self.choices[name]

    def noul(self, name: str) -> float:
        """The calibrated probability for a ``NoulQuestion`` answer."""
        return self.nouls[name]


class DecisionPort(Protocol):
    """Jev (`typesafe_sdk.TypeSafeClient.system_one`) — every routing/gating decision."""

    def decide(self, state: dict[str, Any], questions: dict[str, ChoiceQuestion | NoulQuestion]) -> Decision:
        """Answer every question in one call. Raises ``DecisionError`` on provider failure."""
        ...


class VisionLlmPort(Protocol):
    """DeepSeek (`deepseek-flash`) — reads photos/PDFs into JSON, writes chat replies."""

    def chat_json(
        self, system: str, user_text: str, images: list[bytes], model_cls: type[T], temperature: float = 0.0
    ) -> T:
        """One JSON-mode call validated into ``model_cls``.

        Retries once at ``temperature=0.2`` on a validation/parse failure, then raises
        ``LlmOutputError``. ``images`` are raw bytes (any size); the adapter resizes and
        base64-encodes them.
        """
        ...

    def chat_text(self, system: str, user_text: str) -> str:
        """A plain-text chat reply (question/chit_chat fallback)."""
        ...


class ImageGenPort(Protocol):
    """Gemini `gemini-2.5-flash-image` — feature C scan generation (SCAN_MODE=genai)."""

    def generate(self, image: bytes, prompt: str) -> bytes:
        """Return the generated image's raw bytes (first inline part of the response)."""
        ...


class CostLedgerPort(Protocol):
    """Daily USD spend tracker gating every provider call (``ASSISTANT_DAILY_COST_CAP_USD``)."""

    def add(self, kind: str, usd: float) -> None:
        """Record a provider call's cost against today's total.

        ``kind`` is one of ``deepseek_vision``/``deepseek_text``/``jev``/``gemini``/
        ``deepseek_browser`` — every adapter that spends money calls this so
        ``today_total()`` (and ``by_kind()``) reflect the pipeline's real spend, not just
        DeepSeek's. ``deepseek_browser`` is billed from a different process (the
        ``ai-browser`` container, see ``app.infrastructure.browser_worker``) against the
        same Redis-backed ledger, constructed there from ``REDIS_URL`` alone.
        """
        ...

    def today_total(self) -> float:
        """Today's accumulated USD spend, across every ``kind``."""
        ...

    def by_kind(self) -> dict[str, float]:
        """Today's accumulated USD spend, broken down by ``kind`` — what
        ``scripts/assistant_costs.py`` prints for the owner."""
        ...

    def over_cap(self) -> bool:
        """True once today's spend has reached the configured cap — stop calling providers."""
        ...


class RateLimiterPort(Protocol):
    """Per-user, rolling-hour pipeline-run limiter (``AssistantService.handle_message``).

    Backed by two Redis counters (the current and previous Paris-local hour, see
    ``app.infrastructure.ai.rate_limit``) so the limit is a true rolling window, not a
    fixed-clock-hour bucket that resets to 0 right on the hour.
    """

    def allow(self, user_id: UUID) -> bool:
        """True (and records this run) when the caller is under the limit; False (the
        run is NOT recorded) once the rolling-hour count has reached the limit."""
        ...
