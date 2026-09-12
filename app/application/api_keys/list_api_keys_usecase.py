"""ListApiKeysUseCase — return the caller's own API keys."""

from __future__ import annotations

from uuid import UUID

from app.application.api_keys.dtos import ApiKeyDto
from app.application.api_keys.ports import ApiKeyRepositoryPort


class ListApiKeysUseCase:
    """List all API keys owned by a user, newest first.

    Read-only — no DB session needed, mirrors ListProjectNotesUseCase.
    """

    def __init__(self, api_key_repo: ApiKeyRepositoryPort) -> None:
        self._repo = api_key_repo

    def execute(self, *, user_id: UUID) -> list[ApiKeyDto]:
        """Return the user's API keys as DTOs, newest first."""
        keys = self._repo.list_for_user(user_id)
        return [ApiKeyDto.from_entity(k) for k in keys]
