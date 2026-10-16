"""Shared rate-limit key helpers for Flask-Limiter."""

from __future__ import annotations

import hashlib
import hmac

from flask import current_app, request
from flask_jwt_extended import get_jwt_identity

from app.domain.value_objects.phone_number import normalize_french_phone
from app.infrastructure.rate_limiter import client_ip_bucket, request_user_id


def _caller_bucket() -> str:
    return client_ip_bucket(request.remote_addr) if request.remote_addr else "unknown"


def body_phone() -> str:
    """The normalised ``phone`` of a JSON body ("" when there is none; an unusable one is kept raw)."""
    body = request.get_json(silent=True)
    raw = body.get("phone") if isinstance(body, dict) else None
    if not isinstance(raw, str):
        return ""
    try:
        return normalize_french_phone(raw)
    except ValueError:
        return raw[:32]


def phone_and_caller_key() -> str:
    """Rate-limit key scoped to the number in the body AND the caller's address bucket.

    The per-number caps are shared by every caller, so one stranger can spend a victim's whole
    allowance; this caps what a single source can ask for one number, leaving the rest to the owner.

    The number is not put in the key: it is hashed under the app secret (the same on every worker),
    so the limiter's storage holds no phone number in the clear.
    """
    secret = str(current_app.config.get("SECRET_KEY") or "").encode()
    digest = hmac.new(secret, body_phone().encode(), hashlib.sha256).hexdigest()[:32]
    return f"phone:{digest}:{_caller_bucket()}"


def jwt_user_key() -> str:  # pragma: no cover
    """Rate-limit key scoped to authenticated JWT identity (falls back to IP).

    Not called during tests (RATELIMIT_ENABLED=False in TestingConfig).
    """
    try:
        uid = get_jwt_identity()
    except Exception:
        uid = None
    uid = uid or request_user_id()
    return f"user:{uid}" if uid else _caller_bucket()
