"""Client-facing texts for SMS-code limits, shared by the sign-in, sign-up and invitation routes."""

from __future__ import annotations

import math

from app.domain.exceptions.auth_exceptions import OTP_SCOPE_NETWORK, OTP_SCOPE_SERVICE, OtpThrottledError

#: A wrong, expired, used or locked code all get this one text: telling them apart would
#: reveal whether a code exists for the number. It says what to do in every case.
INVALID_CODE_MESSAGE = "Invalid or expired code. After too many wrong tries, request a new code."


def _wait(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} second" + ("" if seconds == 1 else "s")
    minutes = math.ceil(seconds / 60)
    return f"{minutes} minute" + ("" if minutes == 1 else "s")


def otp_throttled(exc: OtpThrottledError) -> tuple[str, str, dict[str, str]]:
    """Return (error code, message, headers) for a 429 on an SMS-code request.

    The hourly cap can last close to an hour, so it names how long, instead of the
    short resend gap's "wait a minute". Retry-After carries the exact wait for clients.
    """
    headers = {"Retry-After": str(exc.retry_after_seconds)}
    scope = getattr(exc, "scope", None)
    if scope == OTP_SCOPE_SERVICE:
        return (
            "SmsServiceBusy",
            f"The text message service is busy. Try again in {_wait(exc.retry_after_seconds)}.",
            headers,
        )
    if scope == OTP_SCOPE_NETWORK:
        return (
            "NetworkThrottled",
            f"Too many sign-in requests from this network. Try again in {_wait(exc.retry_after_seconds)}.",
            headers,
        )
    if exc.hourly_limit:
        minutes = math.ceil(exc.retry_after_seconds / 60)
        unit = "minute" if minutes == 1 else "minutes"
        return (
            "OtpHourlyLimit",
            f"Too many codes were requested for this number. Try again in {minutes} {unit}.",
            headers,
        )
    return "TooManyRequests", "A code was sent recently. Wait a minute and try again.", headers
