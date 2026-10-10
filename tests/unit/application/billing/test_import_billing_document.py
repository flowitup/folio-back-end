"""Unit tests for ImportBillingDocumentUseCase.

Phase 03 — minimal coverage for the import flow.
Broader tests land in phase 08.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.import_billing_document_usecase import ImportBillingDocumentUseCase
from app.application.billing.dtos import ImportBillingDocumentInput, ItemInput
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.exceptions import (
    BillingDocumentAlreadyExistsError,
    ForbiddenCompanyBillingError,
    ForbiddenProjectAccessError,
    MissingCompanyProfileError,
)

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

    @pytest.mark.parametrize("doc_number", ["FAC202403151230459", "F-2024-2147483646", "FAC-2024-99999999999-A"])
    def test_import_oversized_sequence_keeps_the_counter(self, setup, doc_number):
        """A foreign number the INTEGER counter cannot continue from is imported without moving it."""
        inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number=doc_number)
        result = setup["uc"].execute(inp, setup["session"])
        assert result.document_number == doc_number
        assert setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2024) == 1

    @pytest.mark.parametrize("doc_number", ["FAC-2024-1000000", "FAC-2024-2147483646", "FAC-2024-99999999999"])
    def test_import_refuses_own_format_number_beyond_the_counter(self, setup, doc_number):
        """The counter would hand FAC-2024-1000000 out again once past 999 999: every later facture would 500."""
        inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number=doc_number)
        with pytest.raises(ValueError, match="must not exceed"):
            setup["uc"].execute(inp, setup["session"])
        assert setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2024) == 1

    def test_import_largest_counted_sequence_still_bumps(self, setup):
        inp = _minimal_input(setup["user_id"], setup["company_id"], doc_number="FAC-2024-999999")
        setup["uc"].execute(inp, setup["session"])
        assert setup["counter_repo"].next_value(setup["company_id"], BillingDocumentKind.FACTURE, 2024) == 1_000_000

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


class _Project:
    def __init__(self, owner_id, user_ids=None):
        self.id = uuid4()
        self.owner_id = owner_id
        self.user_ids = user_ids or []


class _ProjectRepo:
    def __init__(self, *projects):
        self._store = {p.id: p for p in projects}

    def find_by_id(self, project_id):
        return self._store.get(project_id)


class TestImportAuthorization:
    """Importing writes the company's billing, so it follows the create rules."""

    def _usecase(self, access_role="admin", projects=()):
        user_id, company_id = uuid4(), uuid4()
        company_repo = InMemoryCompanyRepository()
        company_repo.save(make_company(owner_id=user_id, company_id=company_id))
        access_repo = InMemoryUserCompanyAccessRepository()
        access_repo.save(make_access(user_id, company_id, role=access_role))
        uc = ImportBillingDocumentUseCase(
            doc_repo=InMemoryBillingDocumentRepository(),
            counter_repo=InMemoryBillingNumberCounterRepository(),
            company_repo=company_repo,
            access_repo=access_repo,
            project_repo=_ProjectRepo(*projects),
        )
        return uc, user_id, company_id

    def test_member_of_the_company_cannot_import(self):
        uc, user_id, company_id = self._usecase(access_role="member")
        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(_minimal_input(user_id, company_id), _FakeSession())

    def test_project_the_caller_cannot_read_is_refused(self):
        foreign = _Project(owner_id=uuid4())
        uc, user_id, company_id = self._usecase(projects=[foreign])
        inp = replace(_minimal_input(user_id, company_id), project_id=foreign.id)
        with pytest.raises(ForbiddenProjectAccessError):
            uc.execute(inp, _FakeSession())

    def test_unknown_project_is_a_validation_error_not_a_crash(self):
        uc, user_id, company_id = self._usecase()
        inp = replace(_minimal_input(user_id, company_id), project_id=uuid4())
        with pytest.raises(ValueError):
            uc.execute(inp, _FakeSession())

    def test_admin_can_import_into_a_project_they_belong_to(self):
        uc, user_id, company_id = self._usecase()
        project = _Project(owner_id=uuid4(), user_ids=[user_id])
        uc._project_repo = _ProjectRepo(project)
        inp = replace(_minimal_input(user_id, company_id), project_id=project.id)
        assert uc.execute(inp, _FakeSession()).document_number == "FAC2025001"

    def test_company_admin_can_import_into_any_project_of_their_company(self):
        # Company admins read every project of their company, as on create.
        uc, user_id, company_id = self._usecase()
        project = _Project(owner_id=uuid4())
        project.company_id = company_id
        uc._project_repo = _ProjectRepo(project)
        inp = replace(_minimal_input(user_id, company_id), project_id=project.id)
        assert uc.execute(inp, _FakeSession()).project_id == project.id


class TestImportDateOrder:
    """An imported devis cannot expire, nor a facture fall due, before it is issued (as on create)."""

    def _usecase(self):
        uc, user_id, company_id = TestImportAuthorization()._usecase()
        return uc, user_id, company_id

    def test_devis_valid_until_before_its_issue_date_is_refused(self):
        uc, user_id, company_id = self._usecase()
        inp = replace(
            _minimal_input(user_id, company_id, doc_number="DEV2026001"),
            kind=BillingDocumentKind.DEVIS,
            issue_date=date(2026, 5, 10),
            validity_until=date(2026, 1, 1),
        )
        with pytest.raises(ValueError, match="validity_until cannot be before issue_date"):
            uc.execute(inp, _FakeSession())

    def test_facture_due_before_its_issue_date_is_refused(self):
        uc, user_id, company_id = self._usecase()
        inp = replace(
            _minimal_input(user_id, company_id), issue_date=date(2026, 5, 10), payment_due_date=date(2026, 1, 1)
        )
        with pytest.raises(ValueError, match="payment_due_date cannot be before issue_date"):
            uc.execute(inp, _FakeSession())

    def test_due_date_on_the_issue_date_is_accepted(self):
        uc, user_id, company_id = self._usecase()
        inp = replace(
            _minimal_input(user_id, company_id), issue_date=date(2026, 5, 10), payment_due_date=date(2026, 5, 10)
        )
        assert uc.execute(inp, _FakeSession()).payment_due_date == date(2026, 5, 10)
