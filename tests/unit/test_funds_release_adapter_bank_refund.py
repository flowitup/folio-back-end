"""Unit tests for FundsReleaseAdapter.create_bank_refund_release / delete_bank_refund_release.

Uses the shared function-scoped SQLite `session` fixture from tests/conftest.py
(real SQLAlchemyInvoiceRepository, real DB constraints) rather than mocks, so the
partial-unique-index race path can be exercised with a genuine IntegrityError.
The partial unique index itself is Postgres-only (proven in the migration/phase
02 round-trip); here the adapter's own idempotency guard is what holds under
SQLite, per the phase-04 plan's implementation note.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import InvoiceNumberConflictError
from app.domain.value_objects.invoice_item import InvoiceItem
from app.infrastructure.adapters.funds_release_adapter import FundsReleaseAdapter
from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository
from app.infrastructure.database.models.invoice import InvoiceModel
from app.infrastructure.database.models.project import ProjectModel
from app.infrastructure.database.models.user import UserModel


def _now():
    return datetime.now(timezone.utc)


def _make_user(session) -> UUID:
    user = UserModel(
        id=uuid4(),
        email=f"u{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(user)
    session.flush()
    return user.id


def _make_project(session, owner_id: UUID) -> UUID:
    project = ProjectModel(id=uuid4(), name=f"P-{uuid4().hex[:6]}", owner_id=owner_id, company_id=None)
    session.add(project)
    session.flush()
    return project.id


def _make_source(
    session,
    project_id: UUID,
    user_id: UUID,
    invoice_type: InvoiceType = InvoiceType.MATERIALS_SERVICES,
    issue_date: date = date(2026, 3, 15),
    unit_price: float = 500.0,
    quantity: float = 1.0,
    invoice_number: str | None = None,
) -> Invoice:
    """Persist a raw source invoice row and return it as a domain Invoice."""
    now = _now()
    inv = InvoiceModel(
        id=uuid4(),
        project_id=project_id,
        invoice_number=invoice_number or f"INV-{uuid4().hex[:8]}",
        type=invoice_type.value,
        issue_date=issue_date,
        recipient_name="ACME Supplies",
        items=[{"description": "Materials", "quantity": quantity, "unit_price": unit_price, "vat_rate": 0}],
        created_by=user_id,
        created_at=now,
        updated_at=now,
        refundable_status="refunded",
        refunded_by="bank",
    )
    session.add(inv)
    session.flush()
    return Invoice(
        id=inv.id,
        project_id=project_id,
        invoice_number=inv.invoice_number,
        type=invoice_type,
        issue_date=issue_date,
        recipient_name="ACME Supplies",
        created_by=user_id,
        created_at=now,
        updated_at=now,
        items=[
            InvoiceItem(description="Materials", quantity=Decimal(str(quantity)), unit_price=Decimal(str(unit_price)))
        ],
        refundable_status="refunded",
        refunded_by="bank",
    )


def _occupy_invoice_number(session, project_id: UUID, user_id: UUID, invoice_number: str) -> None:
    """Insert an unrelated row that pre-occupies a specific invoice_number slot."""
    now = _now()
    session.add(
        InvoiceModel(
            id=uuid4(),
            project_id=project_id,
            invoice_number=invoice_number,
            type=InvoiceType.RELEASED_FUNDS.value,
            issue_date=date.today(),
            recipient_name="Occupant",
            items=[],
            created_by=user_id,
            created_at=now,
            updated_at=now,
            is_auto_generated=True,
        )
    )
    session.flush()


@pytest.fixture
def repo(session):
    return SQLAlchemyInvoiceRepository(session)


@pytest.fixture
def adapter(repo):
    return FundsReleaseAdapter(invoice_repo=repo)


class TestCreateBankRefundRelease:
    def test_creates_exactly_one_release_linked_to_source(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id, unit_price=500.0, quantity=2.0)

        adapter.create_bank_refund_release(source, created_by=user_id)

        release = repo.find_bank_refund_release(source.id)
        assert release is not None
        assert release.refunds_invoice_id == source.id
        assert release.is_auto_generated is True
        assert release.type == InvoiceType.RELEASED_FUNDS
        assert release.total_amount == source.total_amount == Decimal("1000")

        # Exactly one row — no duplicate released_funds rows for this source.
        rows = session.query(InvoiceModel).filter(InvoiceModel.refunds_invoice_id == source.id).all()
        assert len(rows) == 1

    def test_calling_twice_is_idempotent(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)

        adapter.create_bank_refund_release(source, created_by=user_id)
        adapter.create_bank_refund_release(source, created_by=user_id)

        rows = session.query(InvoiceModel).filter(InvoiceModel.refunds_invoice_id == source.id).all()
        assert len(rows) == 1

    def test_noop_when_source_type_is_not_materials_services(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id, invoice_type=InvoiceType.LABOR)

        adapter.create_bank_refund_release(source, created_by=user_id)

        assert repo.find_bank_refund_release(source.id) is None

    def test_noop_and_logs_info_when_total_amount_non_positive(self, session, repo, adapter, caplog):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id, unit_price=0.0, quantity=1.0)
        assert source.total_amount <= 0

        with caplog.at_level(logging.INFO):
            adapter.create_bank_refund_release(source, created_by=user_id)

        assert repo.find_bank_refund_release(source.id) is None
        assert "total_amount <= 0" in caplog.text

    def test_fr_number_year_matches_source_issue_date_year_not_current_year(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        # A deliberately non-current year to prove the number follows the SOURCE,
        # not datetime.now().
        source = _make_source(session, project_id, user_id, issue_date=date(2019, 6, 1))

        adapter.create_bank_refund_release(source, created_by=user_id)

        release = repo.find_bank_refund_release(source.id)
        assert release.invoice_number.startswith("FR-2019-")
        assert datetime.now(timezone.utc).year != 2019  # sanity: not a coincidence

    def test_release_description_and_line_shape(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id, unit_price=250.0, quantity=1.0)

        adapter.create_bank_refund_release(source, created_by=user_id)

        release = repo.find_bank_refund_release(source.id)
        assert len(release.items) == 1
        item = release.items[0]
        assert item.description == f"Remboursement banque — {source.invoice_number}"
        assert item.quantity == Decimal("1")
        assert item.unit_price == Decimal("250")
        assert item.vat_rate == Decimal("0")
        assert release.recipient_name == source.recipient_name
        assert release.issue_date == source.issue_date

    def test_integrity_error_on_create_is_swallowed_and_logged(self, session, repo, adapter, monkeypatch, caplog):
        """A race against the partial unique index surfaces as IntegrityError on
        repo.create(); the adapter must swallow it (log warning) and leave the
        session usable — mirrors create_funds_release's existing behaviour.

        This exercises the adapter's generic IntegrityError branch, NOT the
        InvoiceNumberConflictError retry path. The Postgres partial unique index
        isn't enforced under SQLite, so the race is simulated by forcing
        next_funds_release_number to collide with an invoice_number already
        occupied for this project (violating the plain uq_project_invoice_number
        constraint instead). SQLite's error text never contains the string
        'uq_project_invoice_number' (unlike PostgreSQL, which embeds the
        constraint name), so repo.create()'s constraint-name check misses under
        SQLite and it re-raises the raw IntegrityError rather than translating
        it to InvoiceNumberConflictError. On PostgreSQL the identical collision
        DOES translate to InvoiceNumberConflictError — see
        test_invoice_number_conflict_is_retried_and_eventually_succeeds and
        test_invoice_number_conflict_propagates_after_exhausting_retries below
        for that path.

        repo.create()'s own except-block calls session.rollback() to recover
        from the failed flush. Under this fixture's plain (non-savepoint)
        transaction-joining `session`, that rollback lands on the same root
        transaction as everything else in the test, so it also discards the
        fixture rows flushed above (not just the failed insert) — usability is
        therefore proven with a FRESH write-then-read after the failure, not by
        re-reading pre-failure state.
        """
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id, issue_date=date(2026, 3, 15))
        _occupy_invoice_number(session, project_id, user_id, "FR-2026-0001")

        monkeypatch.setattr(repo, "next_funds_release_number", lambda project_id, year=None: "FR-2026-0001")

        with caplog.at_level(logging.WARNING):
            adapter.create_bank_refund_release(source, created_by=user_id)

        assert "rejected by a unique constraint" in caplog.text
        assert repo.find_bank_refund_release(source.id) is None

        # Session must still be usable after the swallowed IntegrityError.
        marker_user_id = _make_user(session)
        assert session.query(UserModel).filter_by(id=marker_user_id).count() == 1

    def test_invoice_number_conflict_is_retried_and_eventually_succeeds(
        self, session, repo, adapter, monkeypatch, caplog
    ):
        """On PostgreSQL, a concurrent request claiming the same sequential FR
        number surfaces as InvoiceNumberConflictError (already translated by
        repo.create(), not a raw IntegrityError). The adapter must retry with a
        freshly generated number rather than let the first conflict propagate.
        """
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)

        real_create = repo.create
        calls = {"n": 0}

        def flaky_create(invoice):
            calls["n"] += 1
            if calls["n"] < 2:
                raise InvoiceNumberConflictError("Invoice number conflict, please retry")
            return real_create(invoice)

        monkeypatch.setattr(repo, "create", flaky_create)

        with caplog.at_level(logging.WARNING):
            adapter.create_bank_refund_release(source, created_by=user_id)

        assert calls["n"] == 2
        assert "retrying" in caplog.text
        release = repo.find_bank_refund_release(source.id)
        assert release is not None
        assert release.refunds_invoice_id == source.id

    def test_invoice_number_conflict_propagates_after_exhausting_retries(self, session, repo, adapter, monkeypatch):
        """When every attempt conflicts, the adapter must not swallow the error —
        the caller (the use-case, then the route) needs InvoiceNumberConflictError
        to surface so it can map it to an HTTP 409 rather than silently returning
        200 with no release created."""
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)

        def always_conflicts(invoice):
            raise InvoiceNumberConflictError("Invoice number conflict, please retry")

        monkeypatch.setattr(repo, "create", always_conflicts)

        with pytest.raises(InvoiceNumberConflictError):
            adapter.create_bank_refund_release(source, created_by=user_id)

        assert repo.find_bank_refund_release(source.id) is None


class TestNextFundsReleaseNumberYearParam:
    def test_without_year_arg_uses_current_utc_year(self, repo):
        """Facture-path regression: omitting `year` must still number by the current year."""
        project_id = uuid4()
        number = repo.next_funds_release_number(project_id)
        current_year = datetime.now(timezone.utc).year
        assert number == f"FR-{current_year}-0001"

    def test_with_explicit_year_overrides_current_year(self, repo):
        project_id = uuid4()
        number = repo.next_funds_release_number(project_id, year=2019)
        assert number == "FR-2019-0001"


class TestDeleteBankRefundRelease:
    def test_delete_when_none_exists_is_noop(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)

        adapter.delete_bank_refund_release(source.id)  # must not raise

        assert repo.find_bank_refund_release(source.id) is None

    def test_delete_removes_the_linked_release(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)
        adapter.create_bank_refund_release(source, created_by=user_id)
        assert repo.find_bank_refund_release(source.id) is not None

        adapter.delete_bank_refund_release(source.id)

        assert repo.find_bank_refund_release(source.id) is None

    def test_delete_never_touches_a_facture_driven_release(self, session, repo, adapter):
        """Facture-driven releases have refunds_invoice_id IS NULL and
        source_billing_document_id set — they must survive any delete call."""
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source = _make_source(session, project_id, user_id)

        now = _now()
        facture_release = InvoiceModel(
            id=uuid4(),
            project_id=project_id,
            invoice_number="FR-2026-9001",
            type=InvoiceType.RELEASED_FUNDS.value,
            issue_date=date.today(),
            recipient_name="Facture Recipient",
            items=[{"description": "Facture payout", "quantity": 1, "unit_price": 999, "vat_rate": 0}],
            created_by=user_id,
            created_at=now,
            updated_at=now,
            source_billing_document_id=None,  # FK target not seeded; irrelevant to this assertion
            refunds_invoice_id=None,
            is_auto_generated=True,
        )
        session.add(facture_release)
        session.flush()
        facture_release_id = facture_release.id

        # Deleting for our (unrelated) source must not touch the facture release.
        adapter.delete_bank_refund_release(source.id)

        survivor = session.query(InvoiceModel).filter_by(id=facture_release_id).first()
        assert survivor is not None
