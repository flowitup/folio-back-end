"""Resend HTTP API email adapter."""

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from app.infrastructure.email.exceptions import EmailDeliveryError
from tasks import EmailPayload

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailAdapter:
    """Sends transactional email via the Resend REST API (stdlib urllib, no SDK)."""

    def __init__(
        self,
        api_key: str,
        from_email: str,
        timeout_seconds: int = 10,
    ) -> None:
        if not api_key:
            raise ValueError("RESEND_API_KEY must not be empty")
        if not from_email:
            raise ValueError("FROM_EMAIL must not be empty")
        self._api_key = api_key
        self._from_email = from_email
        self._timeout = timeout_seconds

    def send(self, payload: EmailPayload) -> None:
        """POST payload to Resend API. Raises EmailDeliveryError on failure."""
        body: dict = {
            "from": payload.from_address or self._from_email,
            "to": [payload.to],
            "subject": payload.subject,
            "text": payload.body,
        }
        if payload.html_body:
            body["html"] = payload.html_body

        data = json.dumps(body).encode("utf-8")
        # Local name `req` to avoid confusion with the imported `urllib.request`
        # module — a previous reviewer flagged the shadowing as N2.
        # Cloudflare (which fronts api.resend.com) returns 403 / error code 1010
        # for stdlib urllib's default "Python-urllib/3.x" User-Agent. Set an
        # explicit non-default UA to get past the WAF bot signature.
        req = urllib.request.Request(
            _RESEND_API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "folio-backend/1.0 (+resend)",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                status = response.status
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            raise EmailDeliveryError(f"Resend API error {exc.code} for <{payload.to}>: {raw}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise EmailDeliveryError(
                f"Resend network error for <{payload.to}>: {exc.reason if hasattr(exc, 'reason') else exc}"
            ) from exc

        if not (200 <= status < 300):
            raise EmailDeliveryError(f"Resend API returned {status} for <{payload.to}>: {raw}")

        try:
            resp_json = json.loads(raw)
            message_id: Optional[str] = resp_json.get("id")
        except (json.JSONDecodeError, AttributeError):
            message_id = None

        logger.info("Email sent via Resend to=<%s> message_id=%s", payload.to, message_id)
