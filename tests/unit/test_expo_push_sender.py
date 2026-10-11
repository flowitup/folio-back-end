"""ExpoPushSender — success logging and the accepted-ticket count."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.application.ports.push_sender import PushMessage
from app.infrastructure.adapters import expo_push_sender
from app.infrastructure.adapters.expo_push_sender import ExpoPushSender


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture(autouse=True)
def _pristine_logger():
    log = expo_push_sender.logger
    saved = (list(log.handlers), log.level, log.propagate)
    log.handlers.clear()
    log.setLevel(logging.NOTSET)
    log.propagate = True
    yield log
    log.handlers[:] = saved[0]
    log.setLevel(saved[1])
    log.propagate = saved[2]


def _message(token: str) -> PushMessage:
    return PushMessage(token=token, title="t", body="b", data={})


def _fake_post(tickets):
    def post(url, json, headers, timeout):
        return httpx.Response(200, json={"data": tickets}, headers={"content-type": "application/json"})

    return post


def test_logs_how_many_tickets_expo_accepted(monkeypatch):
    monkeypatch.setattr(
        expo_push_sender.httpx,
        "post",
        _fake_post([{"status": "ok"}, {"status": "error", "message": "no creds", "details": {}}]),
    )
    sender = ExpoPushSender()
    captured = _ListHandler()
    expo_push_sender.logger.addHandler(captured)

    sender.send([_message("a"), _message("b")])

    assert "expo.push.sent accepted=1 of=2" in captured.lines
    assert any(line.startswith("expo.push.ticket_error") for line in captured.lines)


def test_attaches_an_info_handler_once():
    ExpoPushSender()
    ExpoPushSender()

    assert len(expo_push_sender.logger.handlers) == 1
    assert expo_push_sender.logger.level == logging.INFO
    assert expo_push_sender.logger.propagate is False
