"""Shared in-memory fakes and fixtures for billing application-layer unit tests.

Fakes:
  InMemoryBillingDocumentRepository
  InMemoryBillingTemplateRepository
  InMemoryCompanyProfileRepository
  InMemoryBillingNumberCounterRepository
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
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BillingDocument], int]:
        docs = [d for d in self._store.values() if d.user_id == user_id and d.kind == kind]
        if status is not None:
            docs = [d for d in docs if d.status == status]
        if project_id is not None:
            docs = [d for d in docs if d.project_id == project_id]
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

    Keyed by (user_id, kind_value, year) — mimics the DB composite key.
    """

    def __init__(self):
        self._counters: dict[tuple, int] = {}

    def next_value(self, user_id: UUID, kind: BillingDocumentKind, year: int) -> int:
        key = (user_id, kind.value, year)
        current = self._counters.get(key, 0)
        next_val = current + 1
        self._counters[key] = next_val
        return next_val


class FakePdfRenderer:
    """Returns a minimal PDF byte string without calling ReportLab."""

    def render(self, doc: BillingDocument) -> bytes:
        return b"%PDF-1.4 fake"


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
def profile(profile_repo, user_id):
    p = make_profile(user_id)
    profile_repo.save(p)
    return p
