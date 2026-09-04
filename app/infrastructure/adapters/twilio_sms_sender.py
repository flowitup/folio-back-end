"""Twilio Programmable Messaging adapter (plain REST call, no SDK).

Sender is an alphanumeric ID such as "Folio" (free, no registration needed in France) or a
Twilio number. Failures raise ``SmsSendError`` so the route can answer 503 instead of pretending
a code was sent.
"""

from __future__ import annotations

import logging

import httpx

from app.application.ports.sms_sender import SmsSendError

logger = logging.getLogger(__name__)


class TwilioSmsSender:
    def __init__(self, account_sid: str, auth_token: str, sender: str, timeout_seconds: float = 10.0) -> None:
        if not (account_sid and auth_token and sender):
            raise ValueError("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM are required")
        self._url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        self._auth = (account_sid, auth_token)
        self._sender = sender
        self._timeout = timeout_seconds

    def send(self, to: str, text: str) -> None:
        try:
            response = httpx.post(
                self._url,
                auth=self._auth,
                data={"To": to, "From": self._sender, "Body": text},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.error("twilio.sms.transport_error to=%s error=%s", to, exc)
            raise SmsSendError("SMS provider unreachable") from exc
        if response.status_code >= 300:
            # Body carries Twilio's error code/message; log it, never echo it to the client.
            logger.error("twilio.sms.rejected to=%s status=%s body=%s", to, response.status_code, response.text[:300])
            raise SmsSendError("SMS provider rejected the message")
        logger.info("twilio.sms.sent to=%s", to)
