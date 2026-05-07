"""Repository and session ports (Protocols) for the companies application layer.

All protocols are structural (no runtime_checkable) — type-checked only.
Infrastructure implementations live in app/infrastructure/companies/.

No Flask, SQLAlchemy, or any infrastructure imports are permitted in this file.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any, Optional, Protocol
from uuid import UUID

from app.domain.companies.company import Company
from app.domain.companies.invite_token import CompanyInviteToken
from app.domain.companies.user_company_access import UserCompanyAccess


class CompanyRepositoryPort(Protocol):
    """Persistence contract for Company aggregates."""

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        """Return company by UUID, or None if not found."""
        ...

    def list_all(self, limit: int, offset: int) -> tuple[list[Company], int]:
        """Return paginated companies with total count (admin view)."""
        ...

    def list_attached_for_user(self, user_id: UUID) -> list[tuple[Company, UserCompanyAccess]]:
        """Return (Company, UserCompanyAccess) pairs for a user's attached companies."""
        ...

    def save(self, company: Company) -> Company:
        """Insert or update a company. Returns the persisted instance."""
        ...

    def delete(self, company_id: UUID) -> None:
        """Hard-delete a company by UUID (FK cascades clean up child rows)."""
        ...


class UserCompanyAccessRepositoryPort(Protocol):
    """Persistence contract for UserCompanyAccess join records."""

    def find(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        """Return the access row for (user_id, company_id), or None."""
        ...

    def find_for_update(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        """Return the access row with SELECT FOR UPDATE lock, or None."""
        ...

    def list_for_user(self, user_id: UUID) -> list[UserCompanyAccess]:
        """Return all access rows for a user."""
        ...

    def list_for_company(self, company_id: UUID) -> list[UserCompanyAccess]:
        """Return all access rows for a company."""
        ...

    def save(self, access: UserCompanyAccess) -> UserCompanyAccess:
        """Insert or update an access row. Returns the persisted instance."""
        ...

    def delete(self, user_id: UUID, company_id: UUID) -> None:
        """Hard-delete the access row for (user_id, company_id)."""
        ...

    def clear_primary_for_user(self, user_id: UUID) -> None:
        """Set is_primary=False for ALL access rows belonging to user_id.

        Used inside a transaction by SetPrimaryCompanyUseCase to guarantee
        at most one primary per user atomically.
        """
        ...


class CompanyInviteTokenRepositoryPort(Protocol):
    """Persistence contract for CompanyInviteToken records."""

    def find_active_for_company(self, company_id: UUID) -> Optional[CompanyInviteToken]:
        """Return the single unredeemed token for a company, or None.

        Does not filter by expiry here — expiry check is the use-case responsibility.
        """
        ...

    def find_active_for_company_for_update(self, company_id: UUID) -> Optional[CompanyInviteToken]:
        """Return the single unredeemed token for a company with SELECT FOR UPDATE, or None.

        M1: used by GenerateInviteTokenUseCase (regenerate=True path) to serialise
        concurrent admin calls and prevent the partial-unique IntegrityError 500.
        Does not filter by expiry.
        """
        ...

    def find_by_id_for_update(self, token_id: UUID) -> Optional[CompanyInviteToken]:
        """Return the token with SELECT FOR UPDATE lock, or None."""
        ...

    def list_active(self) -> list[CompanyInviteToken]:
        """Return all active (unredeemed + non-expired) tokens.

        Used by RedeemInviteTokenUseCase to verify plaintext against stored
        argon2 hashes. Bounded by a DOS guard (N ≤ 1000) in the use-case.
        """
        ...

    def save(self, token: CompanyInviteToken) -> CompanyInviteToken:
        """Insert or update a token row. Returns the persisted instance."""
        ...

    def delete(self, token_id: UUID) -> None:
        """Hard-delete a token row by UUID."""
        ...


class Argon2HasherPort(Protocol):
    """Port for argon2 hashing and constant-time verification.

    The existing PasswordHasherPort in app.application.ports.password_hasher
    has the same interface. This alias is defined here so the companies layer
    imports stay self-contained and the infrastructure adapter can satisfy
    either protocol without modification.
    """

    def hash(self, plaintext: str) -> str:
        """Hash a plaintext string. Returns an argon2 encoded hash string."""
        ...

    def verify(self, plaintext: str, hashed: str) -> bool:
        """Verify plaintext against an argon2 hash in constant time.

        Returns True on match, False otherwise. Never raises on mismatch.
        """
        ...


class SecureTokenGeneratorPort(Protocol):
    """Port for cryptographically-secure opaque token generation.

    The production adapter wraps secrets.token_urlsafe(byte_length).
    Test adapters can return deterministic strings.
    """

    def generate(self, byte_length: int = 32) -> str:
        """Return a base64url-encoded string of *byte_length* random bytes."""
        ...


class ClockPort(Protocol):
    """Port for obtaining the current UTC datetime.

    Injected so tests can supply a fixed clock without monkey-patching.
    """

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class RoleCheckerPort(Protocol):
    """Minimal port to ask whether a user holds a specific permission.

    Implementations may back this with the existing AuthorizationService
    or with a simpler permission-set lookup.
    """

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        """Return True if user_id holds *permission* (or '*:*')."""
        ...


class TransactionalSessionPort(Protocol):
    """Minimal session contract shared by all mutating companies use-cases.

    Matches the equivalent port in app.application.billing.ports so the
    infrastructure layer can wire the same db.session to both.
    """

    def begin_nested(self) -> AbstractContextManager[Any]:
        """Open a SAVEPOINT block as a context manager."""
        ...

    def commit(self) -> None:
        """Commit the outer transaction."""
        ...

    def flush(self) -> None:
        """Flush pending changes to the DB without committing."""
        ...
