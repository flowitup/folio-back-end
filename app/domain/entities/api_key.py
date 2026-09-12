"""ApiKey domain entity — models a personal automation credential.

A key inherits its owner's permissions in full: there is no scope, no
read-only mode, and no company pinning (locked product decision). Keys never
expire — there is deliberately no ``expires_at`` field anywhere on this
entity, on the port, on the ORM model, or on the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

_MAX_NAME_LEN = 100


def validate_api_key_name(name: str) -> str:
    """Strip and validate an API key name.

    Shared by ``ApiKey.create`` and ``CreateApiKeyUseCase`` so the cap check
    can run against an already-validated name without constructing a token
    first (see the use-case for why validation must happen before the cap
    check, not after).

    Raises:
        InvalidApiKeyNameError: name is empty after stripping, or exceeds
            ``_MAX_NAME_LEN`` characters.
    """
    # Local import avoids a circular dependency (exceptions -> api_key -> exceptions).
    from app.application.api_keys.exceptions import InvalidApiKeyNameError

    stripped = name.strip()
    if not stripped:
        raise InvalidApiKeyNameError("API key name must not be empty.")
    if len(stripped) > _MAX_NAME_LEN:
        raise InvalidApiKeyNameError(f"API key name must not exceed {_MAX_NAME_LEN} characters.")
    return stripped


@dataclass(frozen=True)
class ApiKey:
    """Immutable personal API key entity.

    The plaintext token is never stored on this entity — only its sha256
    hash (``token_hash``) and a short, non-secret ``prefix`` used to tell
    keys apart in listings without revealing the secret itself.
    """

    id: UUID
    user_id: UUID
    name: str
    prefix: str
    token_hash: str
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def create(
        cls,
        *,
        user_id: UUID,
        name: str,
        prefix: str,
        token_hash: str,
    ) -> ApiKey:
        """Create a new ApiKey with a validated name.

        ``prefix`` and ``token_hash`` are produced by
        ``app.application.api_keys.token_factory.generate_api_key`` — this
        factory only validates and assembles the entity, it never generates
        the token itself (that stays in the application layer).

        Raises:
            InvalidApiKeyNameError: if name is empty (after strip) or exceeds
                100 characters.
        """
        return cls(
            id=uuid4(),
            user_id=user_id,
            name=validate_api_key_name(name),
            prefix=prefix,
            token_hash=token_hash,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
