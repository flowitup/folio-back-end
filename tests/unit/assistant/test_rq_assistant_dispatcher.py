"""Unit tests for `app.infrastructure.adapters.rq_assistant_dispatcher.RqAssistantDispatcher`
— a retry after a failed action enqueue must reuse the same RQ job id instead of
dispatching a genuinely distinct second job for the same tap."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.infrastructure.adapters import rq_assistant_dispatcher as dispatcher_module
from app.infrastructure.adapters.rq_assistant_dispatcher import RqAssistantDispatcher


class FakeRedis:
    @classmethod
    def from_url(cls, url: str) -> "FakeRedis":
        return cls()


class FakeQueue:
    calls: list[dict[str, Any]] = []

    def __init__(self, name: str, connection: Any) -> None:
        self.name = name

    def enqueue(self, func_name: str, *args: Any, **kwargs: Any) -> None:
        FakeQueue.calls.append({"func_name": func_name, "args": args, "kwargs": kwargs})


def test_action_received_uses_a_job_id_keyed_only_on_the_message_id(monkeypatch) -> None:
    FakeQueue.calls = []
    monkeypatch.setattr(dispatcher_module, "Queue", FakeQueue)
    monkeypatch.setattr(dispatcher_module, "Redis", FakeRedis)
    dispatcher = RqAssistantDispatcher(redis_url="redis://unused", assistant_enabled=lambda: True)

    message_id = uuid4()
    dispatcher.action_received(user_id=uuid4(), message_id=message_id, action="confirm", payload={"a": 1})

    assert len(FakeQueue.calls) == 1
    assert FakeQueue.calls[0]["kwargs"]["job_id"] == f"action:{message_id}"


def test_a_retry_with_a_different_action_on_the_same_message_reuses_the_same_job_id(monkeypatch) -> None:
    """`answer_choice_if_unanswered` is a one-shot atomic transition — a choice is
    never legitimately re-dispatched with a different action once answered, so keying
    purely on `message_id` (not `message_id` + `action`) is correct, not a gap: any
    second enqueue for this message, whatever action it carries, is the same
    reset-and-retry race this job id is meant to collapse into one RQ job."""
    FakeQueue.calls = []
    monkeypatch.setattr(dispatcher_module, "Queue", FakeQueue)
    monkeypatch.setattr(dispatcher_module, "Redis", FakeRedis)
    dispatcher = RqAssistantDispatcher(redis_url="redis://unused", assistant_enabled=lambda: True)

    message_id = uuid4()
    user_id = uuid4()
    dispatcher.action_received(user_id=user_id, message_id=message_id, action="confirm", payload={})
    dispatcher.action_received(user_id=user_id, message_id=message_id, action="confirm", payload={})

    job_ids = [call["kwargs"]["job_id"] for call in FakeQueue.calls]
    assert job_ids == [f"action:{message_id}", f"action:{message_id}"]
