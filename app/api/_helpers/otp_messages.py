"""Client-facing texts for SMS-code limits, shared by the sign-in, sign-up and invitation routes."""

from __future__ import annotations

import math

from app.domain.exceptions.auth_exceptions import OtpThrottledError

#: A wrong, expired, used or locked code all get this one text: telling them apart would
#: reveal whether a code exists for the number. It says what to do in every case.
INVALID_CODE_MESSAGE = "Invalid or expired code. After too many wrong tries, request a new code."


def otp_throttled(exc: OtpThrottledError) -> tuple[str, str, dict[str, str]]:
    """Return (error code, message, headers) for a 429 on an SMS-code request.

    The hourly cap can last close to an hour, so it names how long, instead of the
    short resend gap's "wait a minute". Retry-After carries the exact wait for clients.
    """
    headers = {"Retry-After": str(exc.retry_after_seconds)}
    if exc.hourly_limit:
        minutes = math.ceil(exc.retry_after_seconds / 60)
        unit = "minute" if minutes == 1 else "minutes"
        return (
            "OtpHourlyLimit",
            f"Too many codes were requested for this number. Try again in {minutes} {unit}.",
            headers,
        )
    return "TooManyRequests", "A code was sent recently. Wait a minute and try again.", headers
