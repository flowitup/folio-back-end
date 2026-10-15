"""Shared rate-limit key helpers for Flask-Limiter."""

from __future__ import annotations

from flask import request
from flask_jwt_extended import get_jwt_identity

from app.infrastructure.rate_limiter import client_ip_bucket


def _caller_bucket() -> str:
    return client_ip_bucket(request.remote_addr) if request.remote_addr else "unknown"


def jwt_user_key() -> str:  # pragma: no cover
    """Rate-limit key scoped to authenticated JWT identity (falls back to IP).

    Not called during tests (RATELIMIT_ENABLED=False in TestingConfig).
    """
    try:
        uid = get_jwt_identity()
        return f"user:{uid}" if uid else _caller_bucket()
    except Exception:
        return _caller_bucket()
