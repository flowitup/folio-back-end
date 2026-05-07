"""RedeemInviteTokenUseCase — user attaches to a company via a plaintext token.

Token verification strategy (v1 — argon2-only, documented):
  - Fetch all active (unredeemed + non-expired) tokens at redeem time.
  - Verify plaintext against each token_hash in constant time via argon2.
  - Reject if candidate count > 1000 (DOS guard — admin-controlled tokens,
    expected N << 100 in practice).
  - Argon2 verify is ~50 ms; worst-case with N=100 = 5 s (unacceptable at
    scale). Rate limit 5/min/user on the API layer is the primary brute-force
    defence. At N > ~20 active tokens the admin should audit token hygiene.

  Scale assumption: this system is single-tenant with a small admin team.
  If scale demands O(1) lookup later, add a HMAC-SHA256 lookup_hash column
  (indexed) alongside the argon2 token_hash — the "two-column trick" described
  in the phase-02 spec. Re-evaluate when N regularly exceeds 50.

Race safety: find_by_id_for_update locks the matching token row before
asserting redeemed_at and writing user_company_access.
"""

from __future__ import annotations

from uuid import UUID

from app.application.companies.dtos import RedeemInviteTokenInput
from app.application.companies.ports import (
    Argon2HasherPort,
    ClockPort,
    CompanyInviteTokenRepositoryPort,
    TransactionalSessionPort,
    UserCompanyAccessRepositoryPort,
)
from app.domain.companies.exceptions import (
    CompanyAlreadyAttachedError,
    InviteTokenAlreadyRedeemedError,
    InviteTokenExpiredError,
    InviteTokenNotFoundError,
    InviteTokenSystemOverloadError,
)
from app.domain.companies.user_company_access import UserCompanyAccess

_DOS_GUARD_LIMIT = 1000


class RedeemInviteTokenUseCase:
    """Attach the calling user to the company identified by a plaintext token.

    Pre-conditions:
      - plaintext_token must match the argon2 hash of an active token.
      - The token must not be expired and must not have been redeemed.
      - The user must not already be attached to the company.

    Post-conditions:
      - token.redeemed_at and token.redeemed_by are set.
      - A UserCompanyAccess row is inserted (is_primary=True if this is the
        user's first attached company, False otherwise).
      - Both writes happen in the same transaction (db_session.commit()).

    Raises:
        InviteTokenNotFoundError: No active token matched the plaintext.
        InviteTokenExpiredError: Matching token has passed expires_at.
        InviteTokenAlreadyRedeemedError: Matching token already redeemed
            (should not occur via normal flow since only active tokens are
            fetched, but included for defence-in-depth after FOR UPDATE).
        CompanyAlreadyAttachedError: User already has access to this company.
        InviteTokenSystemOverloadError: DOS guard — more than 1000 active tokens in system.
    """

    def __init__(
        self,
        token_repo: CompanyInviteTokenRepositoryPort,
        access_repo: UserCompanyAccessRepositoryPort,
        hasher: Argon2HasherPort,
        clock: ClockPort,
    ) -> None:
        self._token_repo = token_repo
        self._access_repo = access_repo
        self._hasher = hasher
        self._clock = clock

    def execute(
        self,
        inp: RedeemInviteTokenInput,
        db_session: TransactionalSessionPort,
    ) -> None:
        now = self._clock.now()

        # 1. Fetch all active tokens (unredeemed — expiry not filtered by repo)
        candidates = self._token_repo.list_active()
        if len(candidates) > _DOS_GUARD_LIMIT:
            # H3: raise typed domain error → route maps to 503 reason=redeem_overloaded
            raise InviteTokenSystemOverloadError(len(candidates))

        # 2. Find the matching token via constant-time argon2 verify
        matched_id = None
        for candidate in candidates:
            if self._hasher.verify(inp.plaintext_token, candidate.token_hash):
                matched_id = candidate.id
                break  # tokens are unique; stop on first match

        if matched_id is None:
            # H4: always use zero UUID sentinel — never leak a real candidate ID
            raise InviteTokenNotFoundError(UUID(int=0))

        # 3. Re-fetch with SELECT FOR UPDATE to serialise concurrent redeems
        token = self._token_repo.find_by_id_for_update(matched_id)
        if token is None:
            # Row disappeared between list and lock — treat as not found
            raise InviteTokenNotFoundError(matched_id)

        # 4. Defence-in-depth state checks (under lock)
        if token.is_redeemed:
            raise InviteTokenAlreadyRedeemedError(token.id)
        if token.is_expired(now):
            raise InviteTokenExpiredError(token.id)

        # 5. Check user is not already attached to this company
        existing_access = self._access_repo.find(inp.user_id, token.company_id)
        if existing_access is not None:
            raise CompanyAlreadyAttachedError(inp.user_id, token.company_id)

        # 6. Determine is_primary: True only if this is the user's first attachment
        current_accesses = self._access_repo.list_for_user(inp.user_id)
        is_primary = len(current_accesses) == 0

        # 7. Mark token redeemed
        redeemed_token = token.with_updates(
            redeemed_at=now,
            redeemed_by=inp.user_id,
        )
        self._token_repo.save(redeemed_token)

        # 8. Insert user_company_access
        access = UserCompanyAccess(
            user_id=inp.user_id,
            company_id=token.company_id,
            is_primary=is_primary,
            attached_at=now,
        )
        self._access_repo.save(access)

        db_session.commit()
