"""Development / test SMS adapter: the message goes to the application log, never to a phone."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class LoggingSmsSender:
    def send(self, to: str, text: str) -> None:
        logger.warning("SMS (not sent, SMS_PROVIDER=log) to=%s text=%r", to, text)
