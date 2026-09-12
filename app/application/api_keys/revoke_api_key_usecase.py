"""RevokeApiKeyUseCase — permanently delete one of the caller's own API keys."""

from __future__ import annotations

from uuid import UUID

from app.application.api_keys.exceptions import ApiKeyNotFoundError
from app.application.api_keys.ports import ApiKeyRepositoryPort, TransactionalSessionPort


class RevokeApiKeyUseCase:
    """Delete an API key, scoped to its owner.

    A key id that exists but belongs to another user is reported exactly
    like one that does not exist at all — the caller must never learn that a
    given id belongs to someone else.
    """

    def __init__(self, api_key_repo: ApiKeyRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = api_key_repo
        self._db = db_session

    def execute(self, *, user_id: UUID, key_id: UUID) -> None:
        """Delete the API key; raise if it does not exist or is not owned by user_id.

        Raises:
            ApiKeyNotFoundError: key_id does not exist, or belongs to another user.
        """
        api_key = self._repo.find_by_id_for_user(key_id, user_id)
        if api_key is None:
            raise ApiKeyNotFoundError(f"API key {key_id} not found.")

        self._repo.delete(api_key.id)
        self._db.commit()
