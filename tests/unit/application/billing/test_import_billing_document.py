"""Unit tests for ImportBillingDocumentUseCase.

Phase 03 — minimal coverage for the import flow.
Broader tests land in phase 08.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.import_billing_document_usecase import ImportBillingDocumentUseCase
from app.application.billing.dtos import ImportBillingDocumentInput, ItemInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import BillingDocumentAlreadyExistsError, MissingCompanyProfileError

from tests.unit.application.billing.conftest import (
    InMemoryBillingDocumentRepository,
    InMemoryBillingNumberCounterRepository,
    InMemoryCompanyRepository,
    InMemoryUserCompanyAccessRepository,
    _FakeSession,
    make_company,
    make_access,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_usecase(doc_repo, counter_repo, company_repo, access_repo):
    return ImportBillingDocumentUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        company_repo=company_repo,
        access_repo=access_repo,
    )


def _minimal_input(user_id, company_id, doc_number="FAC2025001", status=BillingDocumentStatus.PAID):
    return ImportBillingDocumentInput(
        user_id=user_id,
        kind=BillingDocumentKind.FACTURE,
        recipient_name="ANN ECO CONSTRUCTION",
        items=[
            ItemInput(description="Travaux", quantity=Decimal("1"), unit_price=Decimal("5000"), vat_rate=Decimal("10"))
        ],
        document_number=doc_number,
        status=status,
        company_id=company_id,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestImportBillingDocumentUseCase:
    @pytest.fixture
    def setup(self):
        user_id = uuid4()
        company_id = uuid4()
        doc_repo = InMemoryBillingDocumentRepository()
        counter_repo = InMemoryBillingNumberCounterRepository()
        company_repo = InMemoryCompanyRepository()
        access_repo = InMemoryUserCompanyAccessRepository()
        company = make_company(owner_id=user_id, company_id=company_id)
        company_repo.save(company)
        access_repo.save(make_access(user_id, company_id))
        uc = _make_usecase(doc_repo, counter_repo, company_repo, access_repo)
        session = _FakeSession()
        return dict(
            user_id=user_id,
            company_id=company_id,
            doc_repo=doc_repo,
            counter_repo=counter_repo,
            company_repo=company_repo,
            access_repo=access_repo,
            uc=uc,
            session=session,
        )

    def test_import_saves_document_with_paid_status(self, setup):
        inp = _minimal_input(setup["user_id"], setup["company_id"])
        result = setup["uc"].execute(inp, setup["session"])
        assert result.status == "paid"
        assert result.document_number == "FAC2025001"

    def test_import_bumps_counter(self, setup):
        """After importing FAC2025007, next auto-create should use seq ≥ 8."""
        inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number="FAC2025007")
        setup["uc"].execute(inp, setup["session"])
        # Counter should have been bumped to at least 8
        next_val = setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2025)
        assert next_val >= 8

    def test_import_two_docs_bumps_counter_to_max(self, setup):
        """Import FAC2025001 then FAC2025002; counter ≥ 3."""
        for doc_num in ["FAC2025001", "FAC2025002"]:
            inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number=doc_num)
            setup["uc"].execute(inp, setup["session"])
        next_val = setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2025)
        assert next_val >= 3

    def test_import_unusual_number_skips_counter_bump(self, setup):
        """FAC0026-ANN-2025-11/08 doesn't match year+seq pattern → no counter bump."""
        inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number="FAC0026-ANN-2025-11/08")
        setup["uc"].execute(inp, setup["session"])
        # Counter should still be at default 1 (no bump happened)
        next_val = setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2025)
        assert next_val == 1

    def test_import_preserves_created_at(self, setup):
        historical_dt = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        inp = ImportBillingDocumentInput(
            user_id=setup["user_id"],
            kind=BillingDocumentKind.FACTURE,
            recipient_name="Client",
            items=[
                ItemInput(
                    description="Travaux", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("10")
                )
            ],
            document_number="FAC2025003",
            status=BillingDocumentStatus.PAID,
            company_id=setup["company_id"],
            created_at=historical_dt,
        )
        result = setup["uc"].execute(inp, setup["session"])
        assert result.created_at == historical_dt

    def test_import_preserves_category_on_items(self, setup):
        inp = ImportBillingDocumentInput(
            user_id=setup["user_id"],
            kind=BillingDocumentKind.FACTURE,
            recipient_name="Client",
            items=[
                ItemInput(
                    description="Dépose toiture",
                    quantity=Decimal("1"),
                    unit_price=Decimal("900"),
                    vat_rate=Decimal("10"),
                    category="Toiture",
                )
            ],
            document_number="FAC2025004",
            status=BillingDocumentStatus.PAID,
            company_id=setup["company_id"],
        )
        result = setup["uc"].execute(inp, setup["session"])
        assert result.items[0].category == "Toiture"

    def test_duplicate_import_raises_already_exists(self, setup):
        """IntegrityError on unique constraint → BillingDocumentAlreadyExistsError (409)."""
        inp = _minimal_input(setup["user_id"], setup["company_id"])

        # Repo that raises IntegrityError every time (simulates DB unique violation)
        class _AlwaysConflictRepo:
            def save(self, doc):
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError(
                    "UNIQUE constraint failed",
                    params={},
                    orig=Exception("uix_billing_document_company_kind_number"),
                )

        uc2 = _make_usecase(
            _AlwaysConflictRepo(),
            setup["counter_repo"],
            setup["company_repo"],
            setup["access_repo"],
        )
        with pytest.raises(BillingDocumentAlreadyExistsError):
            uc2.execute(inp, setup["session"])

    def test_missing_company_id_raises(self, setup):
        inp = ImportBillingDocumentInput(
            user_id=setup["user_id"],
            kind=BillingDocumentKind.FACTURE,
            recipient_name="Client",
            items=[
                ItemInput(description="X", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("10"))
            ],
            document_number="FAC2025005",
            status=BillingDocumentStatus.PAID,
            company_id=None,
        )
        with pytest.raises(MissingCompanyProfileError):
            setup["uc"].execute(inp, setup["session"])
