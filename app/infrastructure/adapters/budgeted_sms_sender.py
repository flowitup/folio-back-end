"""SMS sender that spends the global SMS budget before it lets a message through."""

from __future__ import annotations

import logging

from app.application.ports.sms_budget import SmsBudgetPort
from app.application.ports.sms_sender import SmsSenderPort
from app.domain.exceptions.auth_exceptions import OtpThrottledError

logger = logging.getLogger(__name__)


class BudgetedSmsSender:
    """Wraps the real sender. Over budget it raises ``OtpThrottledError``, which every code-sending route
    already answers with its 429 + Retry-After, before anything leaves. Numbers that never get an SMS
    (the store-review accounts) never reach ``send``, so they neither spend nor wait on the budget.
    """

    def __init__(self, sender: SmsSenderPort, budget: SmsBudgetPort) -> None:
        self._sender = sender
        self._budget = budget

    def send(self, to: str, text: str) -> None:
        wait = self._budget.acquire()
        if wait:
            logger.warning("sms_budget.exceeded retry_after=%s", wait)
            raise OtpThrottledError(
                "SMS budget exhausted; try again later", retry_after_seconds=wait, hourly_limit=wait > 60
            )
        self._sender.send(to, text)
