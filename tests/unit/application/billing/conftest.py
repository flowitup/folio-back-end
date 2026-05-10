"""Shared in-memory fakes and fixtures for billing application-layer unit tests.

Fakes:
  InMemoryBillingDocumentRepository
  InMemoryBillingTemplateRepository
  InMemoryCompanyProfileRepository
  InMemoryBillingNumberCounterRepository
  InMemoryCompanyRepository        — for phase-05 company_repo wiring
  InMemoryUserCompanyAccessRepository — for phase-05 access_repo wiring
  FakePdfRenderer
  _FakeSession
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import pytest

from app.domain.billing.company_profile import CompanyProfile
from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.value_objects import BillingDocumentItem
from app.domain.companies.company import Company
from app.domain.companies.user_company_access import UserCompanyAccess


# ---------------------------------------------------------------------------
# In-memory repository implementations
# ---------------------------------------------------------------------------


class InMemoryBillingDocumentRepository:
    """Dict-backed billing document store for unit tests."""

    def __init__(self):
        self._store: dict[UUID, BillingDocument] = {}

    def find_by_id(self, doc_id: UUID) -> Optional[BillingDocument]:
        return self._store.get(doc_id)

    def find_by_id_for_update(self, doc_id: UUID) -> Optional[BillingDocument]:
        """No-op lock — degrade to plain find_by_id in tests."""
        return self.find_by_id(doc_id)

    def list_for_user(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        company_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        docs = [d for d in self._store.values() if d.user_id == user_id and d.kind == kind]
        if status is not None:
            docs = [d for d in docs if d.status == status]
        if project_id is not None:
            docs = [d for d in docs if d.project_id == project_id]
        if company_id is not None:
            docs = [d for d in docs if d.company_id == company_id]
        total = len(docs)
        return docs[offset : offset + limit], total

    def save(self, doc: BillingDocument) -> BillingDocument:
        self._store[doc.id] = doc
        return doc

    def delete(self, doc_id: UUID) -> None:
        self._store.pop(doc_id, None)

    def find_by_source_devis_id(self, devis_id: UUID) -> Optional[BillingDocument]:
        for doc in self._store.values():
            if doc.source_devis_id == devis_id:
                return doc
        return None

    def aggregate_item_suggestions(self, user_id, category, q, limit):
        """In-memory aggregation — delegates to the SQLite path of the real repo."""
        from collections import defaultdict
        from app.application.billing.dtos import (
            ActivityCategoryDTO,
            ActivitySuggestionDTO,
            ActivitySuggestionsResponse,
        )

        docs = [d for d in self._store.values() if d.user_id == user_id]
        groups: dict[tuple, list] = defaultdict(list)
        all_cat_counts: dict[str, int] = defaultdict(int)

        for doc in docs:
            for item in doc.items:
                item_cat = item.category
                desc = item.description
                if category is not None and item_cat != category:
                    continue
                if q and not desc.lower().startswith(q.lower()):
                    continue
                groups[(item_cat, desc)].append((doc.created_at, None, str(item.unit_price), str(item.vat_rate)))
                if item_cat:
                    all_cat_counts[item_cat] += 1

        # Build suggestions
        suggestion_list = []
        for (item_cat, desc), entries in groups.items():
            entries.sort(key=lambda e: e[0] or "", reverse=True)
            last = entries[0]
            suggestion_list.append(
                ActivitySuggestionDTO(
                    description=desc,
                    category=item_cat,
                    frequency=len(entries),
                    last_unit=last[1],
                    last_unit_price=last[2],
                    last_vat_rate=last[3],
                )
            )
        suggestion_list.sort(key=lambda s: (-s.frequency, s.description))

        # Category counts (unfiltered)
        all_cat_counts2: dict[str, int] = defaultdict(int)
        for doc in docs:
            for item in doc.items:
                if item.category:
                    all_cat_counts2[item.category] += 1

        categories = sorted(
            [ActivityCategoryDTO(name=k, frequency=v) for k, v in all_cat_counts2.items()],
            key=lambda c: c.name,
        )[:50]

        return ActivitySuggestionsResponse(
            categories=categories,
            suggestions=suggestion_list[:limit],
        )


class InMemoryBillingTemplateRepository:
    """Dict-backed billing template store for unit tests."""

    def __init__(self):
        self._store: dict[UUID, BillingDocumentTemplate] = {}

    def find_by_id(self, template_id: UUID) -> Optional[BillingDocumentTemplate]:
        return self._store.get(template_id)

    def list_for_user(
        self,
        user_id: UUID,
        kind: Optional[BillingDocumentKind] = None,
    ) -> list[BillingDocumentTemplate]:
        tpls = [t for t in self._store.values() if t.user_id == user_id]
        if kind is not None:
            tpls = [t for t in tpls if t.kind == kind]
        return tpls

    def save(self, template: BillingDocumentTemplate) -> BillingDocumentTemplate:
        self._store[template.id] = template
        return template

    def delete(self, template_id: UUID) -> None:
        self._store.pop(template_id, None)


class InMemoryCompanyProfileRepository:
    """Dict-backed company profile store for unit tests."""

    def __init__(self):
        self._store: dict[UUID, CompanyProfile] = {}

    def find_by_user_id(self, user_id: UUID) -> Optional[CompanyProfile]:
        return self._store.get(user_id)

    def save(self, profile: CompanyProfile) -> CompanyProfile:
        self._store[profile.user_id] = profile
        return profile


class InMemoryBillingNumberCounterRepository:
    """Dict-backed counter repo for unit tests.

    Keyed by (company_id, kind_value, year) — mimics the DB composite key.
    _counters stores next_value (the value that will be returned on next call),
    starting from 1. bump_to_at_least stores desired_next = value + 1.
    """

    def __init__(self):
        # stores next_value to return; absent key → first call returns 1
        self._next: dict[tuple, int] = {}

    def next_value(self, company_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        key = (company_id, kind.value, year)
        val = self._next.get(key, 1)
        self._next[key] = val + 1
        return val

    def bump_to_at_least(
        self,
        company_id: UUID,
        kind: BillingDocumentKind,
        year: int,
        value: int,
    ) -> int:
        """Ensure next_value >= value + 1. Returns resulting next_value."""
        key = (company_id, kind.value, year)
        desired_next = value + 1
        current = self._next.get(key, 1)
        new_next = max(current, desired_next)
        self._next[key] = new_next
        return new_next


class FakePdfRenderer:
    """Returns a minimal PDF byte string without calling ReportLab."""

    def render(self, doc: BillingDocument) -> bytes:
        return b"%PDF-1.4 fake"


class InMemoryCompanyRepository:
    """Lightweight company store for billing unit tests (phase-05 company_repo wiring)."""

    def __init__(self):
        self._store: dict[UUID, Company] = {}

    def find_by_id(self, company_id: UUID) -> Optional[Company]:
        return self._store.get(company_id)

    def find_by_id_for_update(self, company_id: UUID) -> Optional[Company]:
        return self.find_by_id(company_id)

    def list_all(self, limit: int = 50, offset: int = 0) -> tuple[list[Company], int]:
        all_c = list(self._store.values())
        return all_c[offset : offset + limit], len(all_c)

    def list_attached_for_user(self, user_id: UUID) -> list[tuple[Company, UserCompanyAccess]]:
        return []

    def save(self, company: Company) -> Company:
        self._store[company.id] = company
        return company

    def delete(self, company_id: UUID) -> None:
        self._store.pop(company_id, None)


class InMemoryUserCompanyAccessRepository:
    """Lightweight access store for billing unit tests (phase-05 access_repo wiring)."""

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

    def save(self, access: UserCompanyAccess) -> UserCompanyAccess:
        self._store[(access.user_id, access.company_id)] = access
        return access

    def delete(self, user_id: UUID, company_id: UUID) -> None:
        self._store.pop((user_id, company_id), None)

    def clear_primary_for_user(self, user_id: UUID) -> None:
        for key, access in list(self._store.items()):
            if key[0] == user_id and access.is_primary:
                self._store[key] = access.with_updates(is_primary=False)


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
# Shared fixture builders
# ---------------------------------------------------------------------------


def make_company(owner_id: UUID, company_id: Optional[UUID] = None, prefix: str = "") -> Company:
    """Build a minimal Company domain entity for billing unit tests."""
    now = datetime.now(timezone.utc)
    return Company(
        id=company_id or uuid4(),
        legal_name="Test Company SAS",
        address="1 rue de la Paix, 75001 Paris",
        siret=None,
        tva_number=None,
        iban=None,
        bic=None,
        logo_url=None,
        default_payment_terms=None,
        prefix_override=prefix or None,
        created_by=owner_id,
        created_at=now,
        updated_at=now,
    )


def make_access(user_id: UUID, company_id: UUID, is_primary: bool = True) -> UserCompanyAccess:
    """Build a UserCompanyAccess domain entity for billing unit tests."""
    now = datetime.now(timezone.utc)
    return UserCompanyAccess(
        user_id=user_id,
        company_id=company_id,
        is_primary=is_primary,
        attached_at=now,
    )


def make_profile(user_id: UUID, prefix: str = "") -> CompanyProfile:
    now = datetime.now(timezone.utc)
    return CompanyProfile(
        user_id=user_id,
        legal_name="Test Company SAS",
        address="1 rue de la Paix, 75001 Paris",
        created_at=now,
        updated_at=now,
        prefix_override=prefix or None,
    )


def make_item(
    desc: str = "Service",
    qty: str = "1",
    price: str = "100",
    vat: str = "20",
) -> BillingDocumentItem:
    return BillingDocumentItem(
        description=desc,
        quantity=Decimal(qty),
        unit_price=Decimal(price),
        vat_rate=Decimal(vat),
    )


def make_doc(
    user_id: UUID,
    kind: BillingDocumentKind = BillingDocumentKind.DEVIS,
    status: BillingDocumentStatus = BillingDocumentStatus.DRAFT,
    doc_number: str = "DEV-2026-001",
    **overrides,
) -> BillingDocument:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=uuid4(),
        user_id=user_id,
        kind=kind,
        document_number=doc_number,
        status=status,
        issue_date=date(2026, 1, 15),
        created_at=now,
        updated_at=now,
        recipient_name="Client Corp",
        issuer_legal_name="Test Company SAS",
        issuer_address="1 rue de la Paix, 75001 Paris",
        items=(make_item(),),
    )
    defaults.update(overrides)
    return BillingDocument(**defaults)


def make_template(
    user_id: UUID,
    kind: BillingDocumentKind = BillingDocumentKind.DEVIS,
    name: str = "Default Template",
) -> BillingDocumentTemplate:
    now = datetime.now(timezone.utc)
    return BillingDocumentTemplate(
        id=uuid4(),
        user_id=user_id,
        kind=kind,
        name=name,
        created_at=now,
        updated_at=now,
        items=(make_item(),),
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def doc_repo():
    return InMemoryBillingDocumentRepository()


@pytest.fixture
def template_repo():
    return InMemoryBillingTemplateRepository()


@pytest.fixture
def profile_repo():
    return InMemoryCompanyProfileRepository()


@pytest.fixture
def counter_repo():
    return InMemoryBillingNumberCounterRepository()


@pytest.fixture
def pdf_renderer():
    return FakePdfRenderer()


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def other_user_id():
    return uuid4()


@pytest.fixture
def company_repo():
    return InMemoryCompanyRepository()


@pytest.fixture
def access_repo():
    return InMemoryUserCompanyAccessRepository()


@pytest.fixture
def company_id():
    """Stable company UUID reused across billing unit tests."""
    return uuid4()


@pytest.fixture
def seeded_company(company_repo, access_repo, user_id, company_id):
    """Seed a Company + primary UserCompanyAccess for the billing unit test user."""
    company = make_company(owner_id=user_id, company_id=company_id)
    company_repo.save(company)
    access = make_access(user_id=user_id, company_id=company_id, is_primary=True)
    access_repo.save(access)
    return company


@pytest.fixture
def profile(profile_repo, user_id):
    p = make_profile(user_id)
    profile_repo.save(p)
    return p
