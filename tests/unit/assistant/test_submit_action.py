"""Unit tests for `SubmitAssistantActionUseCase` — the atomic choice-answer transition
(review finding NEW-H1). The API-level `test_already_answered_409` in
`tests/api/test_assistant_endpoints.py` already proves the sequential case (submit,
submit again -> 409); this module proves the concurrent case a sequential test cannot
reach: a second writer whose conditional UPDATE is evaluated against the row's *current*
state, not against whatever it read earlier.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import pytest

from app.application.assistant.exceptions import AssistantAlreadyAnsweredError
from app.application.assistant.service import AssistantDispatchFailedError, SubmitAssistantActionUseCase
from app.domain.entities.chat_message import ChannelRef, ChatMessage


class FakeDirectory:
    """Every message in this module lives in ``company:<user_id>`` and is addressed to
    that same user — the only membership fact `SubmitAssistantActionUseCase` needs."""

    def is_member(self, user_id: UUID, channel: ChannelRef) -> bool:
        return channel.id == user_id


class FakeMessageRepo:
    """In-memory double for `MessagePosterPort` whose `answer_choice_if_unanswered`
    mirrors the real repository's atomicity contract: it decides purely from the
    *current* stored payload, never from a value the caller read earlier."""

    def __init__(self) -> None:
        self.messages: dict[UUID, ChatMessage] = {}
        # When set, the next `find_by_id` for this message id also answers the message
        # (as a concurrent request would, between this read and our own write) before
        # returning the stale snapshot the caller already has a reference to.
        self._race_on_next_read: Optional[UUID] = None

    def add(self, message: ChatMessage) -> None:
        self.messages[message.id] = message

    def find_by_id(self, message_id: UUID) -> Optional[ChatMessage]:
        message = self.messages.get(message_id)
        if self._race_on_next_read == message_id:
            self._race_on_next_read = None
            # A concurrent submission "wins" the race right here, after this read has
            # already captured its (now stale) snapshot.
            assert self.answer_choice_if_unanswered(message_id, "cancel", {})
        return message

    def update_payload(self, message_id: UUID, payload: dict[str, Any]) -> None:
        current = self.messages[message_id]
        self.messages[message_id] = replace(current, payload=payload)

    def answer_choice_if_unanswered(self, message_id: UUID, answered: str, answered_payload: dict[str, Any]) -> bool:
        current = self.messages[message_id]
        current_payload = dict(current.payload or {})
        if current_payload.get("answered"):
            return False
        current_payload["answered"] = answered
        current_payload["answered_payload"] = answered_payload
        self.messages[message_id] = replace(current, payload=current_payload)
        return True

    def list_recent_addressed(self, channel: ChannelRef, limit: int = 10) -> list[ChatMessage]:
        return []


class FakeSession:
    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class FakeDispatcher:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.actions_received: list[tuple[UUID, UUID, str, dict[str, Any]]] = []
        self._succeeds = succeeds

    def message_received(self, *, user_id: UUID, message_id: UUID) -> None:  # pragma: no cover - unused here
        pass

    def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> bool:
        self.actions_received.append((user_id, message_id, action, payload))
        return self._succeeds


def _choice_message(user_id: UUID) -> ChatMessage:
    return ChatMessage(
        id=uuid4(),
        channel=ChannelRef(kind="company", id=user_id),
        sender_id=None,
        body="Confirmer ?",
        attachment=None,
        created_at=datetime.now(timezone.utc),
        sender_type="assistant",
        content_type="choice",
        payload={
            "addressed_to": str(user_id),
            "options": [
                {"label": "Confirmer", "action": "confirm", "payload": {}},
                {"label": "Annuler", "action": "cancel", "payload": {}},
            ],
        },
    )


def test_two_sequential_submits_second_raises_already_answered() -> None:
    user_id = uuid4()
    repo = FakeMessageRepo()
    message = _choice_message(user_id)
    repo.add(message)
    dispatcher = FakeDispatcher()
    use_case = SubmitAssistantActionUseCase(repo, FakeDirectory(), FakeSession(), dispatcher)

    use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)
    with pytest.raises(AssistantAlreadyAnsweredError):
        use_case.execute(actor_id=user_id, action="cancel", payload={}, reply_to_id=message.id)

    assert len(dispatcher.actions_received) == 1
    assert dispatcher.actions_received[0] == (user_id, message.id, "confirm", {})


def test_lost_update_race_between_read_and_write_never_double_dispatches() -> None:
    """A naive read-then-write ("read payload, check answered, then write") would let
    this race dispatch twice: `execute`'s own read happens before the concurrent
    request answers the message, so a stale-read check alone would not catch it. The
    atomic `answer_choice_if_unanswered` call must still refuse, because it is
    evaluated against the row's state at write time, not at read time."""
    user_id = uuid4()
    repo = FakeMessageRepo()
    message = _choice_message(user_id)
    repo.add(message)
    dispatcher = FakeDispatcher()
    use_case = SubmitAssistantActionUseCase(repo, FakeDirectory(), FakeSession(), dispatcher)

    # Arm the race: the instant `execute` performs its own `find_by_id` read, a
    # concurrent request answers the message first (simulating another process's
    # winning UPDATE landing between our read and our own write).
    repo._race_on_next_read = message.id

    with pytest.raises(AssistantAlreadyAnsweredError):
        use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)

    # The racer's own answer (simulated inline above) already claimed the message —
    # our request must never dispatch on top of it.
    assert len(dispatcher.actions_received) == 0
    assert repo.messages[message.id].payload["answered"] == "cancel"


def test_dispatch_failure_resets_the_choice_to_unanswered_and_raises() -> None:
    """A 202 followed by a job that never actually runs (assistant disabled, or the
    enqueue itself failing) used to leave the choice permanently `answered` with no
    reply ever coming and no way to retry. `action_received` returning False must undo
    the just-recorded answer and surface a failure the API route maps to 503."""
    user_id = uuid4()
    repo = FakeMessageRepo()
    message = _choice_message(user_id)
    repo.add(message)
    dispatcher = FakeDispatcher(succeeds=False)
    use_case = SubmitAssistantActionUseCase(repo, FakeDirectory(), FakeSession(), dispatcher)

    with pytest.raises(AssistantDispatchFailedError):
        use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)

    assert repo.messages[message.id].payload["answered"] is None
    assert repo.messages[message.id].payload.get("answered_payload") is None

    # The reset must be visible to a retry, and a healthy dispatcher lets it through.
    dispatcher._succeeds = True
    use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)
    assert repo.messages[message.id].payload["answered"] == "confirm"


def test_dispatcher_raising_is_treated_the_same_as_returning_false() -> None:
    class _RaisingDispatcher:
        def message_received(self, *, user_id: UUID, message_id: UUID) -> None:  # pragma: no cover
            pass

        def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> bool:
            raise RuntimeError("redis is down")

    user_id = uuid4()
    repo = FakeMessageRepo()
    message = _choice_message(user_id)
    repo.add(message)
    use_case = SubmitAssistantActionUseCase(repo, FakeDirectory(), FakeSession(), _RaisingDispatcher())

    with pytest.raises(AssistantDispatchFailedError):
        use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)

    assert repo.messages[message.id].payload["answered"] is None


def test_dispatcher_returning_none_is_still_treated_as_success() -> None:
    """Backward compatibility: an adapter/fake that has not been updated to report
    success/failure (still returns `None` implicitly) must behave exactly as before."""

    class _LegacyDispatcher:
        def __init__(self) -> None:
            self.actions_received: list[tuple[UUID, UUID, str, dict[str, Any]]] = []

        def message_received(self, *, user_id: UUID, message_id: UUID) -> None:  # pragma: no cover
            pass

        def action_received(self, *, user_id: UUID, message_id: UUID, action: str, payload: dict[str, Any]) -> None:
            self.actions_received.append((user_id, message_id, action, payload))

    user_id = uuid4()
    repo = FakeMessageRepo()
    message = _choice_message(user_id)
    repo.add(message)
    dispatcher = _LegacyDispatcher()
    use_case = SubmitAssistantActionUseCase(repo, FakeDirectory(), FakeSession(), dispatcher)

    use_case.execute(actor_id=user_id, action="confirm", payload={}, reply_to_id=message.id)

    assert repo.messages[message.id].payload["answered"] == "confirm"
    assert len(dispatcher.actions_received) == 1
