"""Unit tests for FundsReleaseAdapter.create_funds_release / delete_funds_release —
the pre-existing facture-driven flow (billing document PAID/CANCELLED transitions).

Added alongside the phase 01/04 bank-refund release work: this file had no
dedicated adapter-level tests before, leaving these two methods uncovered.
Mirrors the fixture style of test_funds_release_adapter_bank_refund.py.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.domain.entities.invoice import InvoiceType
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


@pytest.fixture
def repo(session):
    return SQLAlchemyInvoiceRepository(session)


@pytest.fixture
def adapter(repo):
    return FundsReleaseAdapter(invoice_repo=repo)


class TestCreateFundsReleaseFactureFlow:
    def test_creates_release_linked_to_billing_document(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source_doc_id = uuid4()

        adapter.create_funds_release(
            project_id=project_id,
            source_doc_id=source_doc_id,
            amount_items=[{"description": "Facture line", "quantity": 2, "unit_price": 100, "vat_rate": 20}],
            recipient_name="Facture Recipient",
            issue_date=date(2026, 4, 1),
            created_by=user_id,
        )

        release = (
            session.query(InvoiceModel)
            .filter(
                InvoiceModel.source_billing_document_id == source_doc_id,
                InvoiceModel.type == InvoiceType.RELEASED_FUNDS.value,
            )
            .first()
        )
        assert release is not None
        assert release.is_auto_generated is True
        assert release.recipient_name == "Facture Recipient"
        assert release.issue_date == date(2026, 4, 1)
        assert release.items[0]["vat_rate"] == 20.0

    def test_integrity_error_on_create_is_swallowed_and_logged(self, session, repo, adapter, monkeypatch, caplog):
        """Mirrors the bank-refund equivalent: repo.create()'s own rollback lands
        on the fixture's shared root transaction, so usability is proven with a
        fresh write-then-read rather than by re-reading pre-failure state."""
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source_doc_id = uuid4()

        now = _now()
        session.add(
            InvoiceModel(
                id=uuid4(),
                project_id=project_id,
                invoice_number="FR-2026-0001",
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
        monkeypatch.setattr(repo, "next_funds_release_number", lambda project_id, year=None: "FR-2026-0001")

        with caplog.at_level(logging.WARNING):
            adapter.create_funds_release(
                project_id=project_id,
                source_doc_id=source_doc_id,
                amount_items=[{"description": "Facture line", "quantity": 1, "unit_price": 500, "vat_rate": 0}],
                recipient_name="Facture Recipient",
                issue_date=date.today(),
                created_by=user_id,
            )

        assert "already exists" in caplog.text

        marker_user_id = _make_user(session)
        assert session.query(UserModel).filter_by(id=marker_user_id).count() == 1


class TestDeleteFundsReleaseFactureFlow:
    def test_delegates_to_repo_delete_by_source_billing_document_id(self, session, repo, adapter):
        user_id = _make_user(session)
        project_id = _make_project(session, user_id)
        source_doc_id = uuid4()
        adapter.create_funds_release(
            project_id=project_id,
            source_doc_id=source_doc_id,
            amount_items=[{"description": "Facture line", "quantity": 1, "unit_price": 200, "vat_rate": 0}],
            recipient_name="Facture Recipient",
            issue_date=date.today(),
            created_by=user_id,
        )

        adapter.delete_funds_release(source_doc_id)

        remaining = session.query(InvoiceModel).filter(InvoiceModel.source_billing_document_id == source_doc_id).first()
        assert remaining is None

    def test_delete_when_none_exists_is_noop(self, adapter):
        adapter.delete_funds_release(uuid4())  # must not raise
