"""JWT token issuer adapter."""

from datetime import timedelta
from typing import Any, Dict, Optional, Set
from uuid import UUID

from flask_jwt_extended import create_access_token, create_refresh_token, decode_token
from flask_jwt_extended.exceptions import JWTDecodeError
from jwt.exceptions import PyJWTError

# In-memory blacklist (replaced with Redis in Phase 03)
_token_blacklist: Set[str] = set()


class JWTTokenIssuer:
    """Flask-JWT-Extended implementation of TokenIssuerPort.

    NOTE: Uses in-memory blacklist for development. For production,
    replace with Redis-backed blacklist (Phase 03).
    """

    def __init__(
        self,
        access_expires_minutes: int = 30,
        refresh_expires_days: int = 7,
    ):
        self._access_expires = timedelta(minutes=access_expires_minutes)
        self._refresh_expires = timedelta(days=refresh_expires_days)

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
            if claims.get("jti") in _token_blacklist:
                return None
            return claims
        except (PyJWTError, JWTDecodeError):
            return None

    def revoke_token(self, jti: str) -> None:
        """Add token to blacklist."""
        _token_blacklist.add(jti)

    def is_token_revoked(self, jti: str) -> bool:
        """Check if token is revoked."""
        return jti in _token_blacklist

    def clear_blacklist(self) -> None:
        """Clear blacklist. For testing only."""
        _token_blacklist.clear()
