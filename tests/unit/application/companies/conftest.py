"""Shared in-memory fakes and fixtures for companies application-layer unit tests.

Fakes:
  InMemoryCompanyRepository
  InMemoryUserCompanyAccessRepository
  FakeRoleService
  FakeClock
  _FakeSession
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.domain.companies.company import Company
from app.domain.companies.user_company_access import UserCompanyAccess


# ---------------------------------------------------------------------------
# In-memory repository implementations
# ---------------------------------------------------------------------------


class InMemoryCompanyRepository:
    """Dict-backed company store for unit tests."""

    def __init__(self):
        self._store: dict[UUID, Company] = {}

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        return self._store.get(company_id)

    def find_by_id_for_update(self, company_id: UUID) -> Optional[Company]:
        return self.find_by_id(company_id)

    def find_by_join_code(self, code: str) -> Optional[Company]:
        for company in self._store.values():
            if company.join_code == code:
                return company
        return None

    def list_all(self, limit: int = 50, offset: int = 0) -> tuple[list[Company], int]:
        all_companies = list(self._store.values())
        total = len(all_companies)
        return all_companies[offset : offset + limit], total

    def list_attached_for_user(self, user_id: UUID) -> list[tuple[Company, UserCompanyAccess]]:
        # Delegate to access repo stored on self if wired; override in tests that need it.
        return []  # overridden by tests that need cross-repo queries

    def save(self, company: Company) -> Company:
        self._store[company.id] = company
        return company

    def delete(self, company_id: UUID) -> None:
        self._store.pop(company_id, None)


class InMemoryUserCompanyAccessRepository:
    """Dict-backed user_company_access store keyed by (user_id, company_id)."""

    def __init__(self):
        self._store: dict[tuple[UUID, UUID], UserCompanyAccess] = {}

    def find(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        return self._store.get((user_id, company_id))

    def find_for_update(self, user_id: UUID, company_id: UUID) -> Optional[UserCompanyAccess]:
        return self.find(user_id, company_id)

    def list_for_user(self, user_id: UUID) -> list[UserCompanyAccess]:
        return [a for (uid, _), a in self._store.items() if uid == user_id]

    def list_for_company(self, company_id: UUID) -> list[UserCompanyAccess]:
        return [a for (_, cid), a in self._store.items() if cid == company_id]

    def list_admins_for_update(self, company_id: UUID) -> list[UserCompanyAccess]:
        """In-memory fake — no real locking, mirrors list_for_company filtered to admins."""
        return [a for a in self.list_for_company(company_id) if a.role == "admin"]

    def save(self, access: UserCompanyAccess) -> UserCompanyAccess:
        self._store[(access.user_id, access.company_id)] = access
        return access

    def delete(self, user_id: UUID, company_id: UUID) -> None:
        self._store.pop((user_id, company_id), None)

    def clear_primary_for_user(self, user_id: UUID) -> None:
        for key, access in list(self._store.items()):
            if access.user_id == user_id and access.is_primary:
                self._store[key] = access.with_updates(is_primary=False)


# ---------------------------------------------------------------------------
# Fake ports
# ---------------------------------------------------------------------------


class FakeRoleService:
    """Controls platform-admin and per-company-admin status for tests."""

    def __init__(self, admin_ids: set[UUID] | None = None):
        self._admin_ids: set[UUID] = admin_ids or set()
        self._company_admins: set[tuple[UUID, UUID]] = set()

    def set_admin(self, user_id: UUID, is_admin: bool = True) -> None:
        """Grant/revoke the legacy global '*:*' wildcard permission."""
        if is_admin:
            self._admin_ids.add(user_id)
        else:
            self._admin_ids.discard(user_id)

    def set_company_admin(self, user_id: UUID, company_id: UUID, is_admin: bool = True) -> None:
        """Grant/revoke a per-company 'admin' role for (user_id, company_id)."""
        if is_admin:
            self._company_admins.add((user_id, company_id))
        else:
            self._company_admins.discard((user_id, company_id))

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        if permission == "*:*":
            return user_id in self._admin_ids
        return False

    def is_platform_admin(self, user_id: UUID) -> bool:
        return user_id in self._admin_ids

    def is_company_admin(self, user_id: UUID, company_id: UUID) -> bool:
        return (user_id, company_id) in self._company_admins


class FakeClock:
    """Fixed-time clock for deterministic tests."""

    def __init__(self, fixed: datetime | None = None):
        self._now = fixed or datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        """Advance the clock by a timedelta expressed as keyword args."""
        self._now += timedelta(**kwargs)


class _FakeSession:
    """Minimal TransactionalSessionPort stub — all ops are no-ops."""

    @contextmanager
    def begin_nested(self):
        yield self

    def commit(self) -> None:
        pass

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def make_company(
    created_by: UUID,
    legal_name: str = "Test Corp SAS",
    siret: str | None = "12345678901234",
    tva_number: str | None = "FR12345678901",
    iban: str | None = "FR7630001007941234567890185",
    bic: str | None = "BNPAFRPP",
    prefix_override: str | None = None,
    **overrides,
) -> Company:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        legal_name=legal_name,
        address="1 rue de la Paix, 75001 Paris",
        siret=siret,
        tva_number=tva_number,
        iban=iban,
        bic=bic,
        logo_url=None,
        default_payment_terms=None,
        prefix_override=prefix_override,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return Company(**defaults)


def make_access(
    user_id: UUID,
    company_id: UUID,
    is_primary: bool = True,
    role: str = "member",
) -> UserCompanyAccess:
    return UserCompanyAccess(
        user_id=user_id,
        company_id=company_id,
        is_primary=is_primary,
        attached_at=datetime.now(timezone.utc),
        role=role,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company_repo():
    return InMemoryCompanyRepository()


@pytest.fixture
def access_repo():
    return InMemoryUserCompanyAccessRepository()


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def admin_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def role_service(admin_id):
    svc = FakeRoleService()
    svc.set_admin(admin_id)
    return svc


@pytest.fixture
def seeded_company(company_repo, admin_id):
    """A company persisted in company_repo, created by admin_id."""
    c = make_company(created_by=admin_id)
    company_repo.save(c)
    return c
