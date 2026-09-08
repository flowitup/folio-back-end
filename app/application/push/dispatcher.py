"""Generic push dispatch: recipients → preference filter → tokens → send off-thread.

Every notifier funnels through here so the cross-cutting concerns exist once: muted
recipients are dropped before any token lookup, delivery never blocks the request, and a
provider failure can never surface to the caller.

Recipients are always resolved by the caller (each domain owns its own scope query, see
`app.infrastructure.database.labor_validation_scope` for the pattern); this module only
decides who still wants the message and how it reaches them.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Iterable, List, Optional, Protocol
from uuid import UUID

from app.application.ports.push_sender import PushMessage, PushSenderPort

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("vi", "fr", "en")


class PushDeviceReaderPort(Protocol):
    def tokens_for_users(self, user_ids: List[UUID]) -> Dict[UUID, List[str]]: ...
    def delete_token(self, token: str) -> None: ...


class NotificationPreferenceReaderPort(Protocol):
    def muted_user_ids(self, user_ids: List[UUID], category: str) -> set[UUID]:
        """Subset of ``user_ids`` that switched ``category`` (or push entirely) off."""
        ...


class PushDispatcher:
    """Send one already-rendered message to a set of users.

    ``run_async=False`` in tests makes delivery synchronous so assertions see the result.
    """

    def __init__(
        self,
        devices: PushDeviceReaderPort,
        sender: PushSenderPort,
        preferences: Optional[NotificationPreferenceReaderPort] = None,
        locale: str = "vi",
        run_async: bool = True,
    ) -> None:
        self._devices = devices
        self._sender = sender
        self._preferences = preferences
        self._locale = locale if locale in SUPPORTED_LOCALES else "vi"
        self._run_async = run_async

    @property
    def locale(self) -> str:
        return self._locale

    @property
    def sender(self) -> PushSenderPort:
        return self._sender

    @sender.setter
    def sender(self, value: PushSenderPort) -> None:
        """Swap the provider after construction.

        Notifiers are built once in `create_app()` with the configured sender, so tests
        (and any future runtime provider switch) need a seam to replace it afterwards.
        """
        self._sender = value

    def dispatch(
        self,
        *,
        category: str,
        recipients: Iterable[UUID],
        title: str,
        body: str,
        data: Dict[str, str],
        exclude: Optional[UUID] = None,
    ) -> None:
        """Deliver to every recipient who still wants ``category``.

        ``exclude`` drops the actor: nobody is notified about their own action. Recipient
        and token resolution stay synchronous (cheap indexed reads, and they must see the
        caller's committed transaction); only the provider call moves off-thread.
        """
        targets = {u for u in recipients if u is not None and u != exclude}
        if not targets:
            return
        if self._preferences is not None:
            try:
                targets -= self._preferences.muted_user_ids(list(targets), category)
            except Exception:
                # A preference lookup failure must not silence a notification.
                logger.exception("push.preferences failed category=%s", category)
            if not targets:
                return

        tokens = self._devices.tokens_for_users(list(targets))
        messages = [
            PushMessage(token=token, title=title, body=body, data=data)
            for user_tokens in tokens.values()
            for token in user_tokens
        ]
        if not messages:
            return
        if self._run_async:
            threading.Thread(target=self._send, args=(messages,), daemon=True).start()
        else:
            self._send(messages)

    def _send(self, messages: List[PushMessage]) -> None:
        try:
            self._sender.send(messages, on_invalid_token=self._forget_token)
        except Exception:  # never let a push failure surface
            logger.exception("push.send failed count=%s", len(messages))

    def _forget_token(self, token: str) -> None:
        try:
            self._devices.delete_token(token)
        except Exception:
            logger.exception("push.forget_token failed")
