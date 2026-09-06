"""Push adapter for dev/test: writes each message to the API log instead of sending it."""

from __future__ import annotations

import logging
from typing import Callable, List, Optional

from app.application.ports.push_sender import PushMessage

logger = logging.getLogger(__name__)


class LoggingPushSender:
    def send(self, messages: List[PushMessage], on_invalid_token: Optional[Callable[[str], None]] = None) -> None:
        for m in messages:
            logger.info(
                "PUSH (not sent, PUSH_PROVIDER=log) to=%s title=%r body=%r data=%s", m.token, m.title, m.body, m.data
            )
