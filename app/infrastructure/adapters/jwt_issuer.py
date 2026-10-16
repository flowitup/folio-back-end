"""JWT token issuer adapter."""

import time
from datetime import timedelta
from typing import Any, Dict, Optional, Set
from uuid import UUID

import redis
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token
from flask_jwt_extended.exceptions import JWTDecodeError
from jwt.exceptions import PyJWTError

# In-memory blacklist fallback for testing
_token_blacklist: Set[str] = set()
# In-memory fallback for rotated refresh tokens: jti -> time it stops being accepted, and
# successor jti -> the jti it replaced (so a logout also ends a predecessor still in its grace).
_rotated_until: Dict[str, float] = {}
_rotated_from: Dict[str, str] = {}

# How long a rotated refresh cookie stays usable. Every open tab's middleware refreshes at the
# access token's half-life, often at the same moment with the same cookie; without a grace the
# slower tabs would present a cookie revoked a few milliseconds earlier and sign the user out.
ROTATION_GRACE_SECONDS = 60
# Bounds the walk back through rotations a logout revokes (a few per grace window at most).
_MAX_ROTATION_CHAIN = 16


def _now() -> float:
    return time.time()


class JWTTokenIssuer:
    """Flask-JWT-Extended implementation of TokenIssuerPort.

    Uses Redis for token blacklist in production.
    Falls back to in-memory for testing when redis_url is None.
    """

    def __init__(
        self,
        access_expires_minutes: int = 7 * 24 * 60,
        refresh_expires_days: int = 7,
        redis_url: Optional[str] = None,
    ):
        self._access_expires = timedelta(minutes=access_expires_minutes)
        self._refresh_expires = timedelta(days=refresh_expires_days)
        self._access_expires_seconds = access_expires_minutes * 60
        self._refresh_expires_seconds = refresh_expires_days * 24 * 60 * 60
        self._redis: Optional[redis.Redis] = None

        if redis_url:
            try:
                self._redis = redis.from_url(redis_url)
                self._redis.ping()  # Test connection
            except redis.RedisError as e:
                import logging

                logging.critical(
                    "Token blacklist: Redis unavailable (%s) — falling back to "
                    "in-memory. Revoked tokens will not persist across restarts "
                    "and are NOT shared between worker processes.",
                    e,
                )
                self._redis = None

    @property
    def access_expires_seconds(self) -> int:
        """Lifetime of the access tokens this issuer signs (what login answers report as expires_in)."""
        return self._access_expires_seconds

    def create_access_token(self, user_id: UUID, additional_claims: Optional[Dict[str, Any]] = None) -> str:
        """Create short-lived access token with user ID and optional claims."""
        claims = additional_claims or {}
        return create_access_token(
            identity=str(user_id),
            additional_claims=claims,
            expires_delta=self._access_expires,
        )

    def create_refresh_token(self, user_id: UUID, persistent: bool = False) -> str:
        """Create a refresh token: 7 days for browsers, never-expiring for persistent (mobile) sessions.

        A persistent token stays valid until the user signs out (logout revokes it for good);
        the ``persistent`` claim tells ``revoke_token`` to keep the blocklist entry without TTL.
        """
        if persistent:
            return create_refresh_token(
                identity=str(user_id),
                expires_delta=False,
                additional_claims={"persistent": True},
            )
        return create_refresh_token(
            identity=str(user_id),
            expires_delta=self._refresh_expires,
        )

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode token. Returns claims or None if invalid/revoked."""
        try:
            claims = decode_token(token)
            jti = claims.get("jti")
            if jti and self.is_token_revoked(jti):
                return None
            return claims
        except (PyJWTError, JWTDecodeError):
            return None

    def revoke_token(self, jti: str, token_type: str = "access", persistent: bool = False) -> None:
        """Add token to blacklist with a TTL matching the token's expiry, at once.

        The TTL follows the token type so a revoked refresh JTI is never forgotten before the
        refresh token itself expires. Persistent refresh tokens never expire, so their entry is
        stored without TTL. A refresh token rotated from earlier ones that are still in their
        grace window (see ``retire_rotated_token``) takes them down with it, so a logout ends
        the whole browser session at once.
        """
        self._blacklist(jti, token_type, persistent)
        if token_type != "refresh":
            return
        current = jti
        for _ in range(_MAX_ROTATION_CHAIN):
            current = self._predecessor_of(current)
            if not current:
                break
            self._blacklist(current, "refresh", persistent=False)

    def retire_rotated_token(
        self, jti: str, successor_jti: Optional[str] = None, grace_seconds: int = ROTATION_GRACE_SECONDS
    ) -> None:
        """Revoke a rotated refresh token ``grace_seconds`` from its FIRST rotation.

        Until then it is still accepted (and each reuse rotates it again) so tabs refreshing
        together with the same cookie all succeed; reusing it never moves the deadline. A plain
        ``revoke_token`` of the token, or of a successor, still revokes it at once.
        """
        until = _now() + grace_seconds
        if self._redis:
            # NX: the first rotation sets the deadline; later reuses inside the window keep it.
            self._redis.set(f"rotated:{jti}", repr(until), nx=True, ex=self._refresh_expires_seconds + grace_seconds)
            if successor_jti:
                self._redis.set(f"rotated_from:{successor_jti}", jti, ex=max(1, grace_seconds))
            return
        _rotated_until.setdefault(jti, until)
        if successor_jti:
            _rotated_from[successor_jti] = jti

    def is_token_revoked(self, jti: str) -> bool:
        """Check if token is revoked (blacklisted, or rotated and past its grace window)."""
        if self._redis:
            blocked, rotated_until = self._redis.mget(f"blacklist:{jti}", f"rotated:{jti}")
            if blocked is not None:
                return True
            return rotated_until is not None and _now() >= float(rotated_until)
        if jti in _token_blacklist:
            return True
        until = _rotated_until.get(jti)
        return until is not None and _now() >= until

    def _blacklist(self, jti: str, token_type: str, persistent: bool) -> None:
        if self._redis:
            if persistent:
                self._redis.set(f"blacklist:{jti}", "1")
                return
            ttl = self._refresh_expires_seconds if token_type == "refresh" else self._access_expires_seconds
            # Store in Redis with TTL (auto-expires when token would expire)
            self._redis.setex(f"blacklist:{jti}", ttl, "1")
        else:
            _token_blacklist.add(jti)

    def _predecessor_of(self, jti: str) -> Optional[str]:
        """The refresh token ``jti`` was rotated from, while that one is still in its grace window."""
        if self._redis:
            found = self._redis.get(f"rotated_from:{jti}")
            return found.decode() if isinstance(found, bytes) else found
        until = _rotated_until.get(_rotated_from.get(jti, ""))
        return _rotated_from[jti] if until is not None and _now() < until else None
