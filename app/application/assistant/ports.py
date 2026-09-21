"""Ports (Protocols) of the assistant application layer.

Everything below `MessagePosterPort` is phase 02: the provider-facing contracts every
`app.infrastructure.ai` adapter implements, plus the small question/answer DTOs that
let `router.py` describe a Jev call without importing `typesafe_sdk` from the
application layer (only the infrastructure adapter — `jev_client.py` — touches the SDK).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar
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

    def list_recent_text(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        """Last ``limit`` text messages of a channel, oldest first (S0 chat history)."""
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


class WebSearchPort(Protocol):
    """Tavily — feature A material search."""

    def search(
        self,
        query: str,
        *,
        include_domains: list[str] | None = None,
        include_images: bool = True,
        max_results: int = 6,
    ) -> dict[str, Any]:
        """``{"results": [{"url","title","content","score"}], "images": [...]}``."""
        ...

    def extract(self, urls: list[str], *, include_images: bool = True) -> dict[str, Any]:
        """``{"results": [{"url","raw_content","images"}], "failed_results": [...]}``."""
        ...


class ImageGenPort(Protocol):
    """Gemini `gemini-2.5-flash-image` — feature C scan generation (SCAN_MODE=genai)."""

    def generate(self, image: bytes, prompt: str) -> bytes:
        """Return the generated image's raw bytes (first inline part of the response)."""
        ...


class LensPort(Protocol):
    """SerpApi Google Lens — feature A fallback when Tavily/Jev pick nothing.

    Needs a PUBLIC image URL (Google Lens fetches it server-side); chat photos live in
    private S3 storage, so the feature layer (phase 03) decides whether/how to produce
    one and simply skips Lens when it cannot.
    """

    def identify(self, image_url: str) -> list[dict[str, Any]]:
        """Reverse-image hits, most relevant first."""
        ...


class CostLedgerPort(Protocol):
    """Daily USD spend tracker gating every provider call (``ASSISTANT_DAILY_COST_CAP_USD``)."""

    def add(self, kind: str, usd: float) -> None:
        """Record a provider call's cost against today's total."""
        ...

    def today_total(self) -> float:
        """Today's accumulated USD spend."""
        ...

    def over_cap(self) -> bool:
        """True once today's spend has reached the configured cap — stop calling providers."""
        ...
