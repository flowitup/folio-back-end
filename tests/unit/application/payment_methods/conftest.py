"""Shared in-memory fakes and fixtures for payment_methods application-layer unit tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.domain.payment_methods.payment_method import PaymentMethod


# ---------------------------------------------------------------------------
# In-memory repository
# ---------------------------------------------------------------------------


class InMemoryPaymentMethodRepository:
    """Dict-backed payment method store for unit tests."""

    def __init__(self):
        self._store: dict[UUID, PaymentMethod] = {}
        # invoice reference counts, keyed by payment_method_id
        self._invoice_counts: dict[UUID, int] = {}

    def find_by_id(self, id: UUID) -> Optional[PaymentMethod]:
        return self._store.get(id)

    def find_by_id_for_update(self, id: UUID) -> Optional[PaymentMethod]:
        return self._store.get(id)

    def find_active_by_company(self, company_id: UUID) -> list[PaymentMethod]:
        return sorted(
            [m for m in self._store.values() if m.company_id == company_id and m.is_active],
            key=lambda m: m.label,
        )

    def find_all_by_company(self, company_id: UUID, *, include_inactive: bool = False) -> list[PaymentMethod]:
        results = [m for m in self._store.values() if m.company_id == company_id]
        if not include_inactive:
            results = [m for m in results if m.is_active]
        return sorted(results, key=lambda m: m.label)

    def find_by_label_ci(self, company_id: UUID, label: str, *, only_active: bool = True) -> Optional[PaymentMethod]:
        for m in self._store.values():
            if m.company_id != company_id:
                continue
            if only_active and not m.is_active:
                continue
            if m.label.lower() == label.lower():
                return m
        return None

    def save(self, method: PaymentMethod) -> PaymentMethod:
        self._store[method.id] = method
        return method

    def insert_many(self, methods: list[PaymentMethod]) -> None:
        for m in methods:
            self._store[m.id] = m

    def count_invoices_referencing(self, payment_method_id: UUID) -> int:
        return self._invoice_counts.get(payment_method_id, 0)

    def find_all_by_company_with_usage_count(
        self, company_id: UUID, *, include_inactive: bool = False
    ) -> list[tuple["PaymentMethod", int]]:
        methods = self.find_all_by_company(company_id, include_inactive=include_inactive)
        return [(m, self._invoice_counts.get(m.id, 0)) for m in methods]

    # test helper
    def set_invoice_count(self, payment_method_id: UUID, count: int) -> None:
        self._invoice_counts[payment_method_id] = count


class FakeRoleService:
    """Controls admin status per user_id for tests."""

    def __init__(self, admin_ids: set[UUID] | None = None):
        self._admin_ids: set[UUID] = admin_ids or set()

    def set_admin(self, user_id: UUID, is_admin: bool = True) -> None:
        if is_admin:
            self._admin_ids.add(user_id)
        else:
            self._admin_ids.discard(user_id)

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        if permission == "*:*":
            return user_id in self._admin_ids
        return False


class FakeUserCompanyAccessRepository:
    """Controls per-(user, company) access for unit tests."""

    def __init__(self, allow_all: bool = True):
        # allow_all=True means every user has access (default for pre-C1 tests)
        self._allowed: set[tuple[UUID, UUID]] = set()
        self._allow_all = allow_all

    def grant(self, user_id: UUID, company_id: UUID) -> None:
        self._allowed.add((user_id, company_id))

    def find(self, user_id: UUID, company_id: UUID) -> object | None:
        if self._allow_all:
            return object()  # truthy — access granted
        return self._allowed.get((user_id, company_id), None) if (user_id, company_id) in self._allowed else None


class FakeCompanyRepository:
    """Controls which company IDs exist for unit tests."""

    def __init__(self, existing_ids: set[UUID] | None = None):
        # None = all IDs exist (default for backwards compat)
        self._existing: set[UUID] | None = existing_ids

    def find_by_id(self, company_id: UUID) -> object | None:
        if self._existing is None:
            return object()  # truthy — company exists
        return object() if company_id in self._existing else None


class _FakeSession:
    """Minimal TransactionalSessionPort stub — all ops are no-ops.

    commit() fires registered post-commit callbacks synchronously so
    post-commit hook tests work without a real DB.
    """

    def __init__(self):
        self._post_commit_callbacks: list = []

    @contextmanager
    def begin_nested(self):
        yield self

    def commit(self) -> None:
        for cb in list(self._post_commit_callbacks):
            cb()
        self._post_commit_callbacks.clear()

    def flush(self) -> None:
        pass

    def after_commit(self, fn) -> None:
        """Register a callback to fire on next commit (used by seeder tests)."""
        self._post_commit_callbacks.append(fn)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def make_payment_method(
    company_id: UUID,
    *,
    label: str = "Cash",
    is_builtin: bool = False,
    is_active: bool = True,
    created_by: Optional[UUID] = None,
    **overrides,
) -> PaymentMethod:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        company_id=company_id,
        label=label,
        is_builtin=is_builtin,
        is_active=is_active,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return PaymentMethod(**defaults)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm_repo():
    return InMemoryPaymentMethodRepository()


@pytest.fixture
def admin_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def company_id():
    return uuid4()


@pytest.fixture
def role_service(admin_id):
    svc = FakeRoleService()
    svc.set_admin(admin_id)
    return svc


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def access_repo():
    """Default: all users have access to all companies."""
    return FakeUserCompanyAccessRepository(allow_all=True)


@pytest.fixture
def company_repo():
    """Default: all company IDs are valid (existing)."""
    return FakeCompanyRepository(existing_ids=None)
