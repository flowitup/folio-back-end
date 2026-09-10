"""SmsGatewaySender: payload/auth shape of the sms-gate.app API and failure → SmsSendError."""

from __future__ import annotations

import httpx
import pytest

from app.application.ports.sms_sender import SmsSendError
from app.infrastructure.adapters.sms_gateway_sender import SmsGatewaySender

URL = "http://192.168.1.32:8080/message"


def _capture_post(monkeypatch, status_code: int, body: str = "{}"):
    """Replace httpx.post with a recorder that answers ``status_code``."""
    calls: list[dict] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return httpx.Response(status_code, text=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    return calls


def test_posts_text_message_with_basic_auth(monkeypatch):
    calls = _capture_post(monkeypatch, 202, '{"id":"abc","state":"Pending"}')

    SmsGatewaySender(URL, "sms", "secret").send("+33640838057", "Folio: ma dang nhap cua ban la 482913.")

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == URL
    assert call["auth"] == ("sms", "secret")
    assert call["json"] == {
        "textMessage": {"text": "Folio: ma dang nhap cua ban la 482913."},
        "phoneNumbers": ["+33640838057"],
    }
    assert call["timeout"] == 10.0


def test_rejected_response_raises(monkeypatch):
    _capture_post(monkeypatch, 401, '{"message":"Unauthorized"}')

    with pytest.raises(SmsSendError):
        SmsGatewaySender(URL, "sms", "wrong").send("+33640838057", "code")


def test_transport_error_raises(monkeypatch):
    def failing_post(url, **kwargs):
        raise httpx.ConnectError("phone offline", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", failing_post)

    with pytest.raises(SmsSendError):
        SmsGatewaySender(URL, "sms", "secret").send("+33640838057", "code")


@pytest.mark.parametrize("url,username,password", [("", "u", "p"), (URL, "", "p"), (URL, "u", "")])
def test_missing_settings_fail_fast(url, username, password):
    with pytest.raises(ValueError):
        SmsGatewaySender(url, username, password)
