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
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Tuple
from uuid import UUID

from app.application.ports.push_sender import PushMessage, PushSenderPort

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("vi", "fr", "en")


class PushDeviceReaderPort(Protocol):
    def tokens_for_users(self, user_ids: List[UUID]) -> Dict[UUID, List[str]]: ...
    def delete_token(self, token: str) -> None: ...

    # Optional: `locales_for_tokens(tokens) -> {token: locale}` — devices that registered
    # their app language. Probed with getattr, so a reader without it keeps the default.


class NotificationPreferenceReaderPort(Protocol):
    def muted_user_ids(self, user_ids: List[UUID], category: str) -> set[UUID]:
        """Subset of ``user_ids`` that switched ``category`` (or push entirely) off."""
        ...


class NotificationEventWriterPort(Protocol):
    def record(
        self, user_ids: Iterable[UUID], *, category: str, kind: str, texts: Dict[str, List[str]], data: Dict[str, str]
    ) -> None: ...


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
        events: Optional[NotificationEventWriterPort] = None,
    ) -> None:
        self._events = events
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
        title: str = "",
        body: str = "",
        data: Dict[str, str],
        exclude: Optional[UUID] = None,
        render: Optional[Callable[[str], Tuple[str, str]]] = None,
    ) -> None:
        """Deliver to every recipient who still wants ``category``.

        ``exclude`` drops the actor: nobody is notified about their own action. Recipient
        and token resolution stay synchronous (cheap indexed reads, and they must see the
        caller's committed transaction); only the provider call moves off-thread.

        ``render(locale) -> (title, body)`` writes the message in each device's own app
        language (the one it registered with, else the server default); without it every
        device gets ``title`` / ``body`` as given.
        """
        targets = {u for u in recipients if u is not None and u != exclude}
        if not targets:
            return
        self._record_events(category, targets, title, body, data, render)
        if self._preferences is not None:
            try:
                targets -= self._preferences.muted_user_ids(list(targets), category)
            except Exception:
                # A preference lookup failure must not silence a notification.
                logger.exception("push.preferences failed category=%s", category)
            if not targets:
                return

        tokens = self._devices.tokens_for_users(list(targets))
        all_tokens = [token for user_tokens in tokens.values() for token in user_tokens]
        locales = self._device_locales(all_tokens) if render is not None else {}
        texts: Dict[str, Tuple[str, str]] = {}
        messages = []
        for token in all_tokens:
            if render is None:
                text = (title, body)
            else:
                locale = locales.get(token, self._locale)
                if locale not in texts:
                    texts[locale] = render(locale)
                text = texts[locale]
            messages.append(PushMessage(token=token, title=text[0], body=text[1], data=data))
        if not messages:
            return
        if self._run_async:
            threading.Thread(target=self._send, args=(messages,), daemon=True).start()
        else:
            self._send(messages)

    def _record_events(
        self,
        category: str,
        targets: set,
        title: str,
        body: str,
        data: Dict[str, str],
        render: Optional[Callable[[str], Tuple[str, str]]],
    ) -> None:
        """Keep a copy in the bell for everyone targeted, whatever their push settings.

        Muting is about interruptions; the bell is where a muted person can still catch up.
        A failure here must never cost the push.
        """
        if self._events is None:
            return
        try:
            texts = {locale: list(render(locale)) if render else [title, body] for locale in SUPPORTED_LOCALES}
            self._events.record(targets, category=category, kind=data.get("kind", category), texts=texts, data=data)
        except Exception:
            logger.exception("notification.record failed category=%s", category)

    def _device_locales(self, tokens: List[str]) -> Dict[str, str]:
        reader = getattr(self._devices, "locales_for_tokens", None)
        if reader is None or not tokens:
            return {}
        try:
            return {t: loc for t, loc in reader(tokens).items() if loc in SUPPORTED_LOCALES}
        except Exception:
            # Falling back to the default language beats losing the notification.
            logger.exception("push.locales failed")
            return {}

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
