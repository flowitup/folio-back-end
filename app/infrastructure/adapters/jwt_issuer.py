"""JWT token issuer adapter."""

from datetime import timedelta
from typing import Any, Dict, Optional, Set
from uuid import UUID

import redis
from flask_jwt_extended import create_access_token, create_refresh_token, decode_token
from flask_jwt_extended.exceptions import JWTDecodeError
from jwt.exceptions import PyJWTError

# In-memory blacklist fallback for testing
_token_blacklist: Set[str] = set()


class JWTTokenIssuer:
    """Flask-JWT-Extended implementation of TokenIssuerPort.

    Uses Redis for token blacklist in production.
    Falls back to in-memory for testing when redis_url is None.
    """

    def __init__(
        self,
        access_expires_minutes: int = 30,
        refresh_expires_days: int = 7,
        redis_url: Optional[str] = None,
    ):
        self._access_expires = timedelta(minutes=access_expires_minutes)
        self._refresh_expires = timedelta(days=refresh_expires_days)
        self._access_expires_seconds = access_expires_minutes * 60
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

    def create_access_token(
        self, user_id: UUID, additional_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create short-lived access token with user ID and optional claims."""
        claims = additional_claims or {}
        return create_access_token(
            identity=str(user_id),
            additional_claims=claims,
            expires_delta=self._access_expires,
        )

    def create_refresh_token(self, user_id: UUID) -> str:
        """Create long-lived refresh token."""
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

    def revoke_token(self, jti: str) -> None:
        """Add token to blacklist with TTL matching access token expiry."""
        if self._redis:
            # Store in Redis with TTL (auto-expires when token would expire)
            self._redis.setex(f"blacklist:{jti}", self._access_expires_seconds, "1")
        else:
            _token_blacklist.add(jti)

    def is_token_revoked(self, jti: str) -> bool:
        """Check if token is revoked."""
        if self._redis:
            return self._redis.exists(f"blacklist:{jti}") > 0
        return jti in _token_blacklist

    def clear_blacklist(self) -> None:
        """Clear blacklist. For testing only."""
        if self._redis:
            # Clear all blacklist keys (pattern match)
            cursor = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match="blacklist:*", count=100)
                if keys:
                    self._redis.delete(*keys)
                if cursor == 0:
                    break
        else:
            _token_blacklist.clear()
