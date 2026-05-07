"""GenerateInviteTokenUseCase — admin generates a single-use invite token."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.application.companies._helpers import _assert_admin
from app.application.companies.dtos import (
    GenerateInviteTokenInput,
    GenerateInviteTokenOutput,
)
from app.application.companies.ports import (
    Argon2HasherPort,
    ClockPort,
    CompanyInviteTokenRepositoryPort,
    CompanyRepositoryPort,
    RoleCheckerPort,
    SecureTokenGeneratorPort,
    TransactionalSessionPort,
)
from app.domain.companies.exceptions import (
    ActiveInviteTokenAlreadyExistsError,
    CompanyNotFoundError,
)
from app.domain.companies.invite_token import CompanyInviteToken

_ADMIN_PERMISSION = "*:*"
_TOKEN_EXPIRY_DAYS = 7
_TOKEN_BYTE_LENGTH = 32


class GenerateInviteTokenUseCase:
    """Generate a new invite token for a company (admin only).

    Behaviour:
      - If regenerate=False and an active token exists: raises
        ActiveInviteTokenAlreadyExistsError (caller must set regenerate=True).
      - If regenerate=True and an active token exists: revokes it first within
        the same transaction, then inserts the new token.
      - Returns GenerateInviteTokenOutput with the plaintext token (shown once).

    Token security model:
      - 32 bytes of cryptographically-secure randomness (SecureTokenGeneratorPort).
      - Stored as argon2 hash (Argon2HasherPort); plaintext is never persisted.
      - DB partial-unique constraint: only one active token per company.
      - Rate limit (10/min admin generate) is enforced at the API layer.
    """

    def __init__(
        self,
        company_repo: CompanyRepositoryPort,
        token_repo: CompanyInviteTokenRepositoryPort,
        hasher: Argon2HasherPort,
        token_generator: SecureTokenGeneratorPort,
        clock: ClockPort,
        role_checker: RoleCheckerPort,
    ) -> None:
        self._company_repo = company_repo
        self._token_repo = token_repo
        self._hasher = hasher
        self._token_generator = token_generator
        self._clock = clock
        self._role_checker = role_checker

    def execute(
        self,
        inp: GenerateInviteTokenInput,
        db_session: TransactionalSessionPort,
    ) -> GenerateInviteTokenOutput:
        # 1. Admin guard
        is_admin = self._role_checker.has_permission(inp.caller_id, _ADMIN_PERMISSION)
        _assert_admin(inp.caller_id, inp.company_id, is_admin)

        # 2. Assert company exists
        company = self._company_repo.find_by_id(inp.company_id)
        if company is None:
            raise CompanyNotFoundError(inp.company_id)

        # 3. Check for existing active token — use FOR UPDATE on regenerate path (M1)
        # to serialise concurrent admin calls and prevent partial-unique IntegrityError.
        if inp.regenerate:
            existing = self._token_repo.find_active_for_company_for_update(inp.company_id)
        else:
            existing = self._token_repo.find_active_for_company(inp.company_id)
        if existing is not None:
            if not inp.regenerate:
                raise ActiveInviteTokenAlreadyExistsError(inp.company_id)
            # Revoke the old token within the same transaction
            self._token_repo.delete(existing.id)

        # 4. Generate plaintext + hash
        plaintext = self._token_generator.generate(_TOKEN_BYTE_LENGTH)
        token_hash = self._hasher.hash(plaintext)

        # 5. Build and persist new token
        now = self._clock.now()
        expires_at = now + timedelta(days=_TOKEN_EXPIRY_DAYS)
        token = CompanyInviteToken(
            id=uuid4(),
            company_id=inp.company_id,
            token_hash=token_hash,
            created_by=inp.caller_id,
            created_at=now,
            expires_at=expires_at,
            redeemed_at=None,
            redeemed_by=None,
        )
        saved = self._token_repo.save(token)
        db_session.commit()

        return GenerateInviteTokenOutput(
            plaintext_token=plaintext,
            token_id=saved.id,
            expires_at=saved.expires_at,
        )
