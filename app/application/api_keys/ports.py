"""Repository and session ports (Protocols) for the api_keys application layer."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.api_key import ApiKey


class ApiKeyRepositoryPort(Protocol):
    """Persistence contract for the ApiKey aggregate."""

    def add(self, api_key: ApiKey) -> None:
        """Insert a new API key."""
        ...

    def list_for_user(self, user_id: UUID) -> list[ApiKey]:
        """Return all API keys owned by user_id, newest first."""
        ...

    def find_by_token_hash(self, token_hash: str) -> Optional[ApiKey]:
        """Look up an API key by its sha256 token hash. Returns None if not found."""
        ...

    def find_by_id_for_user(self, key_id: UUID, user_id: UUID) -> Optional[ApiKey]:
        """Look up an API key by id, scoped to its owner.

        Returns None both when the id does not exist at all and when it
        belongs to a different user — the caller must not be able to tell
        the two cases apart.
        """
        ...

    def delete(self, key_id: UUID) -> None:
        """Delete an API key by id. No-op if not found."""
        ...

    def count_for_user(self, user_id: UUID) -> int:
        """Return the number of API keys owned by user_id."""
        ...

    def touch_last_used(self, key_id: UUID) -> None:
        """Best-effort, throttled update of last_used_at for key_id.

        Implementations must never raise into the caller — see
        ``SqlAlchemyApiKeyRepository.touch_last_used`` for the guarded UPDATE
        + swallow-and-log contract this port promises.
        """
        ...


# Re-export TransactionalSessionPort from invitations to avoid duplication.
# Both modules share the same minimal session contract (begin_nested + commit).
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: E402,F401
