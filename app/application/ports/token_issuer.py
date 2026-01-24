"""Token issuer port - interface for JWT token operations."""

from typing import Any, Dict, Optional, Protocol
from uuid import UUID


class TokenIssuerPort(Protocol):
    """Port for JWT token operations."""

    def create_access_token(
        self, user_id: UUID, additional_claims: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create short-lived access token."""
        ...

    def create_refresh_token(self, user_id: UUID) -> str:
        """Create long-lived refresh token."""
        ...

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify and decode token. Returns claims or None if invalid."""
        ...

    def revoke_token(self, jti: str) -> None:
        """Add token to blacklist by JTI."""
        ...
