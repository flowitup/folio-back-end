"""SMS sender that spends an SMS budget (one pool) before it lets a message through."""

from __future__ import annotations

import logging

from app.application.ports.sms_budget import SmsBudgetPort
from app.application.ports.sms_sender import SmsSenderPort
from app.domain.exceptions.auth_exceptions import OTP_SCOPE_SERVICE, OtpThrottledError

logger = logging.getLogger(__name__)


class BudgetedSmsSender:
    """Wraps the real sender. Over budget it raises ``OtpThrottledError`` (scope "service", answered as
    ``SmsServiceBusy`` so no user is told their own number asked too often), which every code-sending
    route already answers with its 429 + Retry-After, before anything leaves. Numbers that never get an SMS
    (the store-review accounts) never reach ``send``, so they neither spend nor wait on the budget.
    ``pool`` only names the budget in the log line.
    """

    def __init__(self, sender: SmsSenderPort, budget: SmsBudgetPort, pool: str = "signin") -> None:
        self._sender = sender
        self._budget = budget
        self._pool = pool

    def send(self, to: str, text: str) -> None:
        wait = self._budget.acquire()
        if wait:
            # An error, not a warning: the whole platform stops texting codes until the budget refills,
            # so ops must be alerted (error-level lines are the ones the log alerting picks up).
            logger.error("sms_budget.exceeded pool=%s retry_after=%s", self._pool, wait)
            raise OtpThrottledError(
                "SMS budget exhausted; try again later",
                retry_after_seconds=wait,
                hourly_limit=wait > 60,
                scope=OTP_SCOPE_SERVICE,
            )
        self._sender.send(to, text)
