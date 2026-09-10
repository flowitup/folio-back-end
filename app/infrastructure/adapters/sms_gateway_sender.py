"""Adapter for the "SMS Gateway for Android" app (sms-gate.app): the code goes out from a real SIM.

The gateway exposes one JSON endpoint protected by HTTP Basic auth. The same payload works for
every deployment mode, only the URL differs:

* local server on the phone ..... ``http://<phone-ip>:8080/message``
* public cloud relay ............. ``https://api.sms-gate.app/3rdparty/v1/messages``
* self-hosted private server ..... ``https://<host>/3rdparty/v1/messages``

so the whole mode choice is the ``SMS_GATEWAY_URL`` value. The gateway answers 2xx once the message
is *queued* on the device (state "Pending"); delivery itself is asynchronous and not tracked here.
Failures raise ``SmsSendError`` so the route answers 503 instead of pretending a code was sent.
"""

from __future__ import annotations

import logging

import httpx

from app.application.ports.sms_sender import SmsSendError

logger = logging.getLogger(__name__)


class SmsGatewaySender:
    def __init__(self, url: str, username: str, password: str, timeout_seconds: float = 10.0) -> None:
        if not (url and username and password):
            raise ValueError("SMS_GATEWAY_URL, SMS_GATEWAY_USERNAME and SMS_GATEWAY_PASSWORD are required")
        self._url = url
        self._auth = (username, password)
        self._timeout = timeout_seconds

    def send(self, to: str, text: str) -> None:
        try:
            response = httpx.post(
                self._url,
                auth=self._auth,
                json={"textMessage": {"text": text}, "phoneNumbers": [to]},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.error("sms_gateway.transport_error to=%s error=%s", to, exc)
            raise SmsSendError("SMS provider unreachable") from exc
        if response.status_code >= 300:
            # Body carries the gateway's error message; log it, never echo it to the client.
            logger.error("sms_gateway.rejected to=%s status=%s body=%s", to, response.status_code, response.text[:300])
            raise SmsSendError("SMS provider rejected the message")
        logger.info("sms_gateway.queued to=%s", to)
