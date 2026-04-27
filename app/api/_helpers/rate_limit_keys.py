"""Shared rate-limit key helpers for Flask-Limiter."""

from __future__ import annotations

from flask import request
from flask_jwt_extended import get_jwt_identity


def jwt_user_key() -> str:  # pragma: no cover
    """Rate-limit key scoped to authenticated JWT identity (falls back to IP).

    Not called during tests (RATELIMIT_ENABLED=False in TestingConfig).
    """
    try:
        uid = get_jwt_identity()
        return f"user:{uid}" if uid else (request.remote_addr or "unknown")
    except Exception:
        return request.remote_addr or "unknown"
