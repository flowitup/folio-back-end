"""Rate limiting infrastructure."""

import ipaddress
from typing import Optional

from flask import current_app, request
from flask_limiter import Limiter


def client_ip_bucket(address: Optional[str]) -> str:
    """Rate-limit bucket of a client address.

    An IPv4 address is its own bucket. An IPv6 client usually controls a whole /64 and can pick
    any address in it, so a per-address bucket is free to rotate: the bucket is the /64 network
    (an IPv4-mapped address maps to its IPv4). Anything unparseable is kept as is.
    """
    if not address:
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(ip), 64), strict=False))
    return str(ip)


def remote_address_bucket() -> str:
    """Key function: the bucket of ``request.remote_addr`` (after the trusted-proxy fix-up)."""
    return client_ip_bucket(request.remote_addr)


# Per-address caps shared by every route that texts an unauthenticated caller, so that rotating
# between the routes (or between numbers) does not multiply them. Overridable by SMS_SEND_IP_LIMITS.
DEFAULT_SMS_SEND_IP_LIMITS = "20 per hour;60 per day"


def sms_send_ip_limits() -> str:
    return current_app.config.get("SMS_SEND_IP_LIMITS") or DEFAULT_SMS_SEND_IP_LIMITS


def code_request_answered(response) -> bool:
    """``deduct_when`` of the code-request caps: only an answered request (202, or the 409 that tells a
    sign-up number already has an account) spends them, not a 400, the resend-gap 429 or a 503.

    A user who taps resend before the countdown ends must not lose the hour's allowance for codes that
    were never issued. The 409 stays counted so probing for registered numbers is still capped.
    """
    return response.status_code in (202, 409)


# storage_uri is omitted here so flask-limiter reads RATELIMIT_STORAGE_URI
# from the Flask app config at init_app() time (allows test overrides)
limiter = Limiter(
    key_func=remote_address_bucket,
    default_limits=["100 per minute"],
)
