"""Unit tests for CreateCompanyUseCase post-commit seed behaviour.

Covers:
- creating a company invokes the seeder
- seeder failure does NOT roll back the company
- company created without seeder injected (legacy DI) still works
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime  # noqa: F401 — used implicitly by Company._validate_*
from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.application.companies.create_company_usecase import CreateCompanyUseCase
from app.application.companies.dtos import CreateCompanyInput
from app.domain.companies.company import Company
from app.domain.companies.exceptions import ForbiddenCompanyError


# ---------------------------------------------------------------------------
# Minimal in-memory fakes (self-contained; no dependency on companies/conftest)
# ---------------------------------------------------------------------------


class _InMemoryCompanyRepo:
    def __init__(self):
        self._store: dict[UUID, Company] = {}

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        return self._store.get(company_id)

    def find_by_id_for_update(self, company_id: UUID) -> Optional[Company]:
        return self._store.get(company_id)

    def list_all(self, limit=50, offset=0):
        return list(self._store.values()), len(self._store)

    def save(self, company: Company) -> Company:
        self._store[company.id] = company
        return company

    def delete(self, company_id: UUID) -> None:
        self._store.pop(company_id, None)

    def list_attached_for_user(self, user_id: UUID):
        return []


class _FakeRoleService:
    def __init__(self, admin_ids=None):
        self._admin_ids: set[UUID] = set(admin_ids or [])

    def has_permission(self, user_id: UUID, permission: str) -> bool:
        return user_id in self._admin_ids if permission == "*:*" else False


class _FakeSession:
    """Session whose commit() fires registered post-commit callbacks synchronously."""

    def __init__(self):
        self._callbacks: list = []

    @contextmanager
    def begin_nested(self):
        yield self

    def commit(self) -> None:
        for cb in list(self._callbacks):
            cb()
        self._callbacks.clear()

    def flush(self) -> None:
        pass


class _SpySeeder:
    """Records every call to execute()."""

    def __init__(self, raise_on_call: bool = False):
        self.calls: list[dict] = []
        self._raise = raise_on_call

    def execute(self, company_id, legal_name, created_by, db_session):
        self.calls.append(
            {
                "company_id": company_id,
                "legal_name": legal_name,
                "created_by": created_by,
            }
        )
        if self._raise:
            raise RuntimeError("Seeder exploded")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def company_repo():
    return _InMemoryCompanyRepo()


@pytest.fixture
def admin_id():
    return uuid4()


@pytest.fixture
def role_service(admin_id):
    svc = _FakeRoleService(admin_ids=[admin_id])
    return svc


@pytest.fixture
def fake_session():
    return _FakeSession()


def _inp(caller_id, *, legal_name="Dupont SARL", address="1 rue de la Paix"):
    return CreateCompanyInput(
        caller_id=caller_id,
        legal_name=legal_name,
        address=address,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateCompanyInvokesSeeder:
    def test_seeder_invoked_after_company_created(self, company_repo, role_service, admin_id, fake_session):
        seeder = _SpySeeder()
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=seeder,
        )

        result = uc.execute(_inp(admin_id), fake_session)

        assert len(seeder.calls) == 1
        call = seeder.calls[0]
        assert call["company_id"] == result.id
        assert call["legal_name"] == "Dupont SARL"
        assert call["created_by"] == admin_id

    def test_seeder_receives_correct_legal_name(self, company_repo, role_service, admin_id, fake_session):
        seeder = _SpySeeder()
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=seeder,
        )

        uc.execute(_inp(admin_id, legal_name="ACME Corp"), fake_session)

        assert seeder.calls[0]["legal_name"] == "ACME Corp"


class TestSeederFailureDoesNotRollBack:
    def test_seeder_failure_swallowed_company_still_created(self, company_repo, role_service, admin_id, fake_session):
        seeder = _SpySeeder(raise_on_call=True)
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=seeder,
        )

        # Must NOT raise even though seeder throws
        result = uc.execute(_inp(admin_id), fake_session)

        # Company was created successfully despite seeder failure
        stored = company_repo.find_by_id(result.id)
        assert stored is not None
        assert stored.legal_name == "Dupont SARL"

    def test_seeder_failure_returns_company_response(self, company_repo, role_service, admin_id, fake_session):
        seeder = _SpySeeder(raise_on_call=True)
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=seeder,
        )

        result = uc.execute(_inp(admin_id), fake_session)

        assert result.legal_name == "Dupont SARL"


class TestCreateCompanyWithoutSeeder:
    def test_works_without_seeder_injected(self, company_repo, role_service, admin_id, fake_session):
        """Legacy DI: seed_payment_methods=None must not raise."""
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=None,
        )

        result = uc.execute(_inp(admin_id), fake_session)

        assert result.legal_name == "Dupont SARL"
        stored = company_repo.find_by_id(result.id)
        assert stored is not None

    def test_non_admin_still_raises_without_seeder(self, company_repo, role_service, fake_session):
        user_id = uuid4()  # not in admin set
        uc = CreateCompanyUseCase(
            company_repo=company_repo,
            role_checker=role_service,
            seed_payment_methods=None,
        )

        with pytest.raises(ForbiddenCompanyError):
            uc.execute(_inp(user_id), fake_session)
