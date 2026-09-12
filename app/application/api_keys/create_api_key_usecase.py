"""CreateApiKeyUseCase — mint a new personal automation API key."""

from __future__ import annotations

from uuid import UUID

from app.application.api_keys.dtos import CreatedApiKeyDto
from app.application.api_keys.exceptions import ApiKeyLimitReachedError
from app.application.api_keys.ports import ApiKeyRepositoryPort, TransactionalSessionPort
from app.application.api_keys.token_factory import generate_api_key
from app.domain.entities.api_key import ApiKey, validate_api_key_name

# A user may hold at most this many API keys at once.
MAX_API_KEYS_PER_USER = 20


class CreateApiKeyUseCase:
    """Create a new API key for a user.

    The key inherits the owner's permissions in full — there is no scope,
    read-only mode, or company pinning, and it never expires (locked product
    decisions).


    The per-user cap is a guardrail against unbounded growth, not a security
    boundary: it is checked and then written without a lock, so two truly
    simultaneous creates can both observe `count == cap - 1` and leave the user
    one key over. Serialising this would cost a lock on every create to defend
    a limit whose only job is to stop a runaway script, so the race is accepted
    deliberately. Nothing downstream reads the count.
    """

    def __init__(self, api_key_repo: ApiKeyRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = api_key_repo
        self._db = db_session

    def execute(self, *, user_id: UUID, name: str) -> CreatedApiKeyDto:
        """Create and persist a new API key; return its DTO with the one-time plaintext token.

        Name is validated before the cap is checked and before a token is
        generated: an invalid name should never be shadowed by a limit error,
        and there is no reason to burn a token generation on a request that
        was going to be rejected anyway.

        Raises:
            InvalidApiKeyNameError: name is empty (after strip) or exceeds 100 characters.
            ApiKeyLimitReachedError: user already holds MAX_API_KEYS_PER_USER keys.
        """
        validated_name = validate_api_key_name(name)

        if self._repo.count_for_user(user_id) >= MAX_API_KEYS_PER_USER:
            raise ApiKeyLimitReachedError(
                f"User {user_id} already holds the maximum of {MAX_API_KEYS_PER_USER} API keys."
            )

        plaintext, prefix, token_hash = generate_api_key()
        api_key = ApiKey.create(
            user_id=user_id,
            name=validated_name,
            prefix=prefix,
            token_hash=token_hash,
        )
        self._repo.add(api_key)
        self._db.commit()
        return CreatedApiKeyDto.from_creation(api_key, plaintext)
