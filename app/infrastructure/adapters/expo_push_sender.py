"""Expo push service adapter — one HTTPS call per batch of 100 messages, no SDK.

Expo relays to APNs / FCM with the credentials configured on the EAS project, so the
backend only needs the Expo push tokens the app registers. ``DeviceNotRegistered``
tickets hand the dead token back through ``on_invalid_token`` so the row gets deleted.
"""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

import httpx

from app.application.ports.push_sender import PushMessage

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_BATCH = 100


class ExpoPushSender:
    def __init__(self, access_token: str = "", timeout_seconds: float = 10.0) -> None:
        self._headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if access_token:
            self._headers["Authorization"] = f"Bearer {access_token}"
        self._timeout = timeout_seconds

    def send(self, messages: List[PushMessage], on_invalid_token: Optional[Callable[[str], None]] = None) -> None:
        for start in range(0, len(messages), _BATCH):
            batch = messages[start : start + _BATCH]
            payload = [
                {
                    "to": m.token,
                    "title": m.title,
                    "body": m.body,
                    "data": m.data,
                    "sound": "default",
                    "priority": "high",
                }
                for m in batch
            ]
            try:
                response = httpx.post(EXPO_PUSH_URL, json=payload, headers=self._headers, timeout=self._timeout)
            except httpx.HTTPError as exc:
                logger.error("expo.push.transport_error count=%s error=%s", len(batch), exc)
                return
            if response.status_code >= 300:
                logger.error("expo.push.rejected status=%s body=%s", response.status_code, response.text[:300])
                return
            tickets = (
                response.json().get("data", [])
                if response.headers.get("content-type", "").startswith("application/json")
                else []
            )
            for message, ticket in zip(batch, tickets):
                if ticket.get("status") == "ok":
                    continue
                details = ticket.get("details") or {}
                logger.warning("expo.push.ticket_error to=%s message=%s", message.token, ticket.get("message"))
                if details.get("error") == "DeviceNotRegistered" and on_invalid_token is not None:
                    on_invalid_token(message.token)
            logger.info("expo.push.sent count=%s", len(batch))
