"""Unit tests for ResendEmailAdapter — mocked urllib."""

from __future__ import annotations

import json
import logging
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.infrastructure.email.exceptions import EmailDeliveryError
from app.infrastructure.email.resend_adapter import ResendEmailAdapter
from tasks import EmailPayload


_API_KEY = "re_test_supersecretkey_12345"
_FROM = "noreply@example.com"


def _make_adapter() -> ResendEmailAdapter:
    return ResendEmailAdapter(api_key=_API_KEY, from_email=_FROM)


def _make_payload(**kwargs) -> EmailPayload:
    defaults = dict(
        to="recipient@example.com",
        subject="Hello",
        body="Plain text body",
        html_body="<p>HTML body</p>",
    )
    defaults.update(kwargs)
    return EmailPayload(**defaults)


def _fake_200_response(body: dict | None = None) -> MagicMock:
    """Return a mock urllib response with status 200."""
    raw = json.dumps(body or {"id": "msg_test_123"}).encode()
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = raw
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Constructor guards
# ---------------------------------------------------------------------------

class TestConstructorGuards:
    def test_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="RESEND_API_KEY"):
            ResendEmailAdapter(api_key="", from_email=_FROM)

    def test_empty_from_email_raises(self):
        with pytest.raises(ValueError, match="FROM_EMAIL"):
            ResendEmailAdapter(api_key=_API_KEY, from_email="")


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

class TestSendSuccess:
    def test_200_response_does_not_raise(self):
        adapter = _make_adapter()
        payload = _make_payload()

        with patch("urllib.request.urlopen", return_value=_fake_200_response()):
            # Must not raise
            adapter.send(payload)

    def test_correct_payload_shape_sent(self):
        """Assert the POST body matches the Resend API contract."""
        adapter = _make_adapter()
        payload = _make_payload(
            to="dest@example.com",
            subject="Test Subject",
            body="Plain text",
            html_body="<b>HTML</b>",
        )

        captured_request: list = []

        def fake_urlopen(req, timeout=None):
            captured_request.append(req)
            return _fake_200_response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            adapter.send(payload)

        assert len(captured_request) == 1
        req = captured_request[0]
        body = json.loads(req.data.decode("utf-8"))

        assert body["from"] == _FROM
        assert body["to"] == ["dest@example.com"]
        assert body["subject"] == "Test Subject"
        assert body["text"] == "Plain text"
        assert body["html"] == "<b>HTML</b>"

    def test_no_html_body_omits_html_field(self):
        adapter = _make_adapter()
        payload = _make_payload(html_body=None)

        captured_request: list = []

        def fake_urlopen(req, timeout=None):
            captured_request.append(req)
            return _fake_200_response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            adapter.send(payload)

        body = json.loads(captured_request[0].data.decode())
        assert "html" not in body

    def test_authorization_header_sent(self):
        adapter = _make_adapter()

        captured_request: list = []

        def fake_urlopen(req, timeout=None):
            captured_request.append(req)
            return _fake_200_response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            adapter.send(_make_payload())

        req = captured_request[0]
        assert req.get_header("Authorization") == f"Bearer {_API_KEY}"

    def test_payload_from_address_overrides_default(self):
        adapter = _make_adapter()
        payload = _make_payload(from_address="custom@sender.com")

        captured_request: list = []

        def fake_urlopen(req, timeout=None):
            captured_request.append(req)
            return _fake_200_response()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            adapter.send(payload)

        body = json.loads(captured_request[0].data.decode())
        assert body["from"] == "custom@sender.com"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

class TestSendErrors:
    def test_http_429_raises_email_delivery_error(self):
        adapter = _make_adapter()

        http_err = urllib.error.HTTPError(
            url="https://api.resend.com/emails",
            code=429,
            msg="Too Many Requests",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b'{"message":"rate limited"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(EmailDeliveryError):
                adapter.send(_make_payload())

    def test_url_error_raises_email_delivery_error(self):
        adapter = _make_adapter()

        url_err = urllib.error.URLError(reason="Name or service not known")

        with patch("urllib.request.urlopen", side_effect=url_err):
            with pytest.raises(EmailDeliveryError):
                adapter.send(_make_payload())

    def test_non_2xx_status_raises_email_delivery_error(self):
        adapter = _make_adapter()
        resp = MagicMock()
        resp.status = 500
        resp.read.return_value = b'{"message":"server error"}'
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(EmailDeliveryError):
                adapter.send(_make_payload())


# ---------------------------------------------------------------------------
# Security: API key must NEVER leak into error messages or logs
# ---------------------------------------------------------------------------

class TestApiKeyNotLeaked:
    def test_api_key_not_in_http_error_message(self):
        adapter = _make_adapter()

        http_err = urllib.error.HTTPError(
            url="https://api.resend.com/emails",
            code=429,
            msg="Too Many Requests",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b'{"message":"rate limited"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(EmailDeliveryError) as exc_info:
                adapter.send(_make_payload())

        assert _API_KEY not in str(exc_info.value)

    def test_api_key_not_in_url_error_message(self):
        adapter = _make_adapter()

        url_err = urllib.error.URLError(reason="Network unreachable")

        with patch("urllib.request.urlopen", side_effect=url_err):
            with pytest.raises(EmailDeliveryError) as exc_info:
                adapter.send(_make_payload())

        assert _API_KEY not in str(exc_info.value)

    def test_api_key_not_in_log_output(self, caplog):
        adapter = _make_adapter()
        http_err = urllib.error.HTTPError(
            url="https://api.resend.com/emails",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b'{"message":"server error"}'),
        )

        with caplog.at_level(logging.WARNING, logger="app.infrastructure.email.resend_adapter"):
            with patch("urllib.request.urlopen", side_effect=http_err):
                with pytest.raises(EmailDeliveryError):
                    adapter.send(_make_payload())

        for record in caplog.records:
            assert _API_KEY not in record.getMessage()
