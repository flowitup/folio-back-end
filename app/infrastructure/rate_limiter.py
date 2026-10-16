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


_NO_IDENTITY = ""
_IDENTITY_ENVIRON_KEY = "folio.rate_limit_user_id"


def _token_from_request() -> Optional[str]:
    """The JWT a request presents: ``Authorization: Bearer`` first, then the access (or, on the
    refresh route, refresh) cookie. ``None`` when there is none."""
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        token = header[7:].strip()
        if token:
            return token
    for name in (
        current_app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie"),
        current_app.config.get("JWT_REFRESH_COOKIE_NAME", "refresh_token_cookie"),
    ):
        token = request.cookies.get(name)
        if token:
            return token
    return None


def request_user_id() -> Optional[str]:
    """The user a request is signed in as, read from its JWT without enforcing anything else.

    flask-limiter checks the default limits in its own ``before_request`` hook, before any
    ``@jwt_required`` has run, so ``get_jwt_identity()`` is not available there. The token's
    signature is still checked (``decode_token``), so a forged token cannot pick another user's
    bucket; an expired, revoked-looking or malformed token simply gives no identity. The answer
    is memoised on the request (not on ``g``, which a test's long-lived app context can share).
    """
    cached = request.environ.get(_IDENTITY_ENVIRON_KEY)
    if cached is not None:
        return cached or None
    uid = _NO_IDENTITY
    try:
        token = _token_from_request()
        if token:
            from flask_jwt_extended import decode_token

            claims = decode_token(token)
            identity = claims.get(current_app.config.get("JWT_IDENTITY_CLAIM", "sub"))
            if identity:
                uid = str(identity)
    except Exception:  # any decode problem: no identity, the caller's address is used
        uid = _NO_IDENTITY
    request.environ[_IDENTITY_ENVIRON_KEY] = uid
    return uid or None


def user_or_address_key() -> str:
    """Default key function: the signed-in user when the request carries a valid JWT, else the
    caller's address bucket.

    Keying signed-in traffic on the address made everyone behind one office router, site Wi-Fi
    or carrier NAT share (and use up) the same quota, including users of other companies. Only
    anonymous requests (sign-in, invitations, health checks) still share an address bucket.
    """
    uid = request_user_id()
    return f"user:{uid}" if uid else remote_address_bucket()


def is_cors_preflight() -> bool:
    """Request filter: an OPTIONS request (a CORS preflight) does no work and is answered by
    flask-cors, so it must not spend the quota of the request that follows it."""
    return request.method == "OPTIONS"


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
    key_func=user_or_address_key,
    default_limits=["100 per minute"],
)
limiter.request_filter(is_cors_preflight)


def request_succeeded(response) -> bool:
    """``deduct_when`` for write limits: only a request that went through spends the quota.

    A refused request (validation error, duplicate, forbidden) changed nothing, so it must not
    leave the user locked out of the corrected retry.
    """
    return response.status_code < 400
