"""Unit tests for `app.application.assistant.jobs` — the RQ entry points' own
`FEATURE_ASSISTANT` gate (review finding NEW-H3). `RqAssistantDispatcher` already
refuses to *enqueue* while the flag is off; these tests prove the *consumption* side: a
job that somehow reaches a worker (enqueued before the flag flipped, or a stale job
sitting in Redis) must not touch `AssistantService`/the invoice-fetch feature while the
flag (or either API key) is off.

`create_app`/`get_container` are imported locally inside each entry point (so the web
process never has to import the heavier AI pipeline just to enqueue a job) — patched
here at their source modules (`app.create_app`, `wiring.get_container`), which is where
those local imports resolve them from at call time.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from flask import Flask

from app.application.assistant import jobs


class _FakeAssistantService:
    def __init__(self) -> None:
        self.handle_message_calls: list[tuple[Any, Any]] = []
        self.handle_action_calls: list[tuple[Any, Any, Any, Any]] = []

    def handle_message(self, *, user_id: Any, message_id: Any) -> None:
        self.handle_message_calls.append((user_id, message_id))

    def handle_action(self, *, user_id: Any, message_id: Any, action: Any, payload: Any) -> None:
        self.handle_action_calls.append((user_id, message_id, action, payload))


class _FakeInvoiceFetchFeature:
    def __init__(self) -> None:
        self.on_result_calls: list[Any] = []

    def on_result(self, job_id: Any, *, messenger: Any, trace_id: Any) -> None:
        self.on_result_calls.append(job_id)


class _FakeContainer:
    def __init__(self) -> None:
        self.assistant_service: Any = _FakeAssistantService()
        self.assistant_invoice_fetch_feature: Any = _FakeInvoiceFetchFeature()
        self.assistant_messenger: Any = object()


def _fake_app(*, feature_assistant: bool, deepseek_key: str = "key", typesafe_key: str = "key") -> Flask:
    app = Flask(__name__)
    app.config["FEATURE_ASSISTANT"] = feature_assistant
    app.config["DEEPSEEK_API_KEY"] = deepseek_key
    app.config["TYPESAFE_API_KEY"] = typesafe_key
    return app


@pytest.fixture()
def fake_container(monkeypatch: pytest.MonkeyPatch) -> _FakeContainer:
    container = _FakeContainer()
    monkeypatch.setattr("wiring.get_container", lambda: container)
    return container


class TestFeatureFlagOff:
    def test_handle_message_is_a_noop(self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.handle_message(str(uuid4()), str(uuid4()))
        assert fake_container.assistant_service.handle_message_calls == []

    def test_handle_action_is_a_noop(self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.handle_action(str(uuid4()), str(uuid4()), "confirm", {})
        assert fake_container.assistant_service.handle_action_calls == []

    def test_process_fetched_invoice_is_a_noop(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=False))
        jobs.process_fetched_invoice(str(uuid4()))
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == []

    @pytest.mark.parametrize("missing_key", ["DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"])
    def test_handle_message_is_a_noop_when_a_key_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer, missing_key: str
    ) -> None:
        keys = {"DEEPSEEK_API_KEY": "key", "TYPESAFE_API_KEY": "key"}
        keys[missing_key] = ""
        monkeypatch.setattr(
            "app.create_app",
            lambda: _fake_app(
                feature_assistant=True, deepseek_key=keys["DEEPSEEK_API_KEY"], typesafe_key=keys["TYPESAFE_API_KEY"]
            ),
        )
        jobs.handle_message(str(uuid4()), str(uuid4()))
        assert fake_container.assistant_service.handle_message_calls == []


class TestFeatureFlagOn:
    def test_handle_message_dispatches_to_the_service(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        user_id, message_id = uuid4(), uuid4()
        jobs.handle_message(str(user_id), str(message_id))
        assert fake_container.assistant_service.handle_message_calls == [(user_id, message_id)]

    def test_handle_action_dispatches_to_the_service(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        user_id, message_id = uuid4(), uuid4()
        jobs.handle_action(str(user_id), str(message_id), "confirm", {"a": 1})
        assert fake_container.assistant_service.handle_action_calls == [(user_id, message_id, "confirm", {"a": 1})]

    def test_process_fetched_invoice_dispatches_to_the_feature(
        self, monkeypatch: pytest.MonkeyPatch, fake_container: _FakeContainer
    ) -> None:
        monkeypatch.setattr("app.create_app", lambda: _fake_app(feature_assistant=True))
        job_id = uuid4()
        jobs.process_fetched_invoice(str(job_id))
        assert fake_container.assistant_invoice_fetch_feature.on_result_calls == [job_id]
