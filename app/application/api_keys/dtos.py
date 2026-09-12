"""Data Transfer Objects for api_keys use-case results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities.api_key import ApiKey


@dataclass(frozen=True)
class ApiKeyDto:
    """Read model returned by api_key use-cases. Never carries the token or its hash."""

    id: UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_entity(cls, api_key: ApiKey) -> ApiKeyDto:
        """Build an ApiKeyDto from an ApiKey entity."""
        return cls(
            id=api_key.id,
            name=api_key.name,
            prefix=api_key.prefix,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
        )


@dataclass(frozen=True)
class CreatedApiKeyDto(ApiKeyDto):
    """ApiKeyDto plus the one-time plaintext token; returned only from creation.

    The plaintext token is emitted exactly once in the create response body —
    it is never persisted or re-derivable afterwards (only its hash is
    stored), so this is the only place it ever appears again after
    generation.
    """

    token: str

    @classmethod
    def from_creation(cls, api_key: ApiKey, token: str) -> CreatedApiKeyDto:
        """Build a CreatedApiKeyDto from a freshly created ApiKey + its plaintext token."""
        return cls(
            id=api_key.id,
            name=api_key.name,
            prefix=api_key.prefix,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            token=token,
        )
