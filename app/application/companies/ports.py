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
from app.domain.companies.user_company_access import UserCompanyAccess


class CompanyRepositoryPort(Protocol):
    """Persistence contract for Company aggregates."""

    def find_by_join_code(self, code: str) -> Optional[Company]:
        """Company whose join code equals ``code`` (already normalised), or None."""
        ...

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

    def list_admins_for_update(self, company_id: UUID) -> list[UserCompanyAccess]:
        """Return the company's admin-role access rows with SELECT FOR UPDATE.

        Used to atomically count remaining admins before demoting, booting, or
        self-detaching an admin, so concurrent last-admin removals cannot both
        succeed and leave the company with zero admins. On SQLite ``FOR UPDATE``
        is a no-op (single-writer), which is acceptable for tests.
        """
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


class Argon2HasherPort(Protocol):
    """Port for argon2 hashing and constant-time verification.

    User sign-in has no password any more (phone + SMS code only), so this is
    now the only hashing protocol in the codebase — it exists here, scoped to
    the companies layer, for hashing company invite tokens.
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

    def is_platform_admin(self, user_id: UUID) -> bool:
        """Return True if user_id carries the platform-ops flag (`users.is_platform_ops`)."""
        ...

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        """Return True if user_id's per-company role for company_id is 'admin'.

        Does NOT imply platform admin — callers combine both checks when a
        platform-admin bypass is also desired (see _helpers._assert_company_admin).
        """
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
