"""Email adapter that writes each e-mail to the API log instead of sending it (development)."""

import logging

from tasks import EmailPayload

logger = logging.getLogger(__name__)


class LoggingEmailAdapter:
    """EMAIL_PROVIDER=log: the e-mail (with its links) lands in the log, like SMS and push "log"."""

    def send(self, payload: EmailPayload) -> None:
        logger.warning(
            "EMAIL (not sent, EMAIL_PROVIDER=log) to=%s subject=%s\n%s",
            getattr(payload, "to", "?"),
            getattr(payload, "subject", ""),
            getattr(payload, "body", ""),
        )
