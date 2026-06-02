"""Unit tests for TagSummaryUseCase — cost rollup calculation correctness.

This module validates that per-tag cost calculations (labor + expenses) are
computed correctly. Tests seed real labor entries and invoices, then verify
the summary calculations match hand-computed expectations.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.entities.project_tag import ProjectTag
from app.application.tags.exceptions import NotProjectMemberError
from app.application.tags.tag_summary_usecase import TagSummaryUseCase
from app.infrastructure.database.models import Base
from app.infrastructure.database.repositories.sqlalchemy_project_tag_repository import (
    SqlAlchemyProjectTagRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_project_membership_reader import (
    SqlAlchemyProjectMembershipReader,
)


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def summary_session():
    """Create an isolated in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_project_and_users(session, project_owner_id: UUID) -> UUID:
    """Create a project with owner; return project_id."""
    from app.infrastructure.database.models import ProjectModel, UserModel

    owner = UserModel(
        email=f"owner-{str(project_owner_id)[:8]}@test.com",
        password_hash="hash",
        is_active=True,
    )
    session.add(owner)
    session.flush()

    project = ProjectModel(
        name="Summary Test Project",
        owner_id=owner.id,
    )
    session.add(project)
    session.flush()

    # Add owner as project member
    from sqlalchemy import text
    from datetime import datetime, timezone

    session.execute(
        text(
            "INSERT INTO user_projects "
            "(user_id, project_id, role_id, invited_by_user_id, assigned_at) "
            "VALUES (:uid, :pid, :rid, NULL, :at) "
        ),
        {
            "uid": str(owner.id),
            "pid": str(project.id),
            "rid": str(uuid4()),  # Dummy role ID (not checked in this test)
            "at": datetime.now(timezone.utc),
        },
    )
    session.commit()
    return project.id, owner.id


def _create_worker(session, project_id: UUID, daily_rate: Decimal) -> UUID:
    """Create a worker; return worker_id."""
    from app.infrastructure.database.models import WorkerModel

    worker = WorkerModel(
        project_id=project_id,
        name="Test Worker",
        daily_rate=float(daily_rate),
    )
    session.add(worker)
    session.flush()
    return worker.id


def _create_labor_entry(
    session,
    worker_id: UUID,
    shift_type: str | None = "normal",
    tag_id: UUID | None = None,
    amount_override: Decimal | None = None,
    date_offset_days: int = 0,
) -> UUID:
    """Create a labor entry; return entry_id.

    date_offset_days: offset from today (0 = today, 1 = tomorrow, -1 = yesterday)
    """
    from app.infrastructure.database.models import LaborEntryModel
    from datetime import date, timedelta

    work_date = date.today() + timedelta(days=date_offset_days)
    entry = LaborEntryModel(
        worker_id=worker_id,
        date=work_date,
        shift_type=shift_type,
        tag_id=tag_id,
        amount_override=float(amount_override) if amount_override else None,
    )
    session.add(entry)
    session.flush()
    return entry.id


def _create_invoice(
    session,
    project_id: UUID,
    items: list[dict],
    tag_id: UUID | None = None,
) -> UUID:
    """Create an invoice with given items; return invoice_id.

    items: [{"quantity": int, "unit_price": float}, ...]
    """
    from app.infrastructure.database.models import InvoiceModel
    from datetime import date

    # Use a unique invoice number based on UUID to avoid uniqueness constraint
    inv_number = f"TEST-{str(uuid4())[:8]}"

    invoice = InvoiceModel(
        project_id=project_id,
        invoice_number=inv_number,
        type="materials_services",
        issue_date=date.today(),
        recipient_name="Test Recipient",
        items=items,
        tag_id=tag_id,
    )
    session.add(invoice)
    session.flush()
    return invoice.id


# ---------------------------------------------------------------------------
# Test: Empty project summary
# ---------------------------------------------------------------------------


class TestEmptySummary:
    def test_empty_project_returns_empty_rows(self, summary_session):
        """Project with no tags, labor, or invoices returns no rows."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows == []

    def test_membership_check_raises_error(self, summary_session):
        """Non-member cannot access summary."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        # Create another user not a member of the project
        from app.infrastructure.database.models import UserModel

        outsider = UserModel(
            email="outsider@test.com",
            password_hash="hash",
            is_active=True,
        )
        summary_session.add(outsider)
        summary_session.commit()

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        with pytest.raises(NotProjectMemberError):
            usecase.execute(actor_id=outsider.id, project_id=project_id)


# ---------------------------------------------------------------------------
# Test: Tags with zero activity
# ---------------------------------------------------------------------------


class TestTagsWithZeroActivity:
    def test_created_tags_with_no_labor_or_invoices(self, summary_session):
        """Tags with zero costs still appear in summary."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)

        # Create two tags
        tag1 = ProjectTag.create(
            project_id=project_id,
            name="Fondations",
            color="#FF0000",
        )
        tag2 = ProjectTag.create(
            project_id=project_id,
            name="Charpente",
            color="#00FF00",
        )
        tag_repo.add(tag1)
        tag_repo.add(tag2)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert len(rows) == 2

        # Both should have zero costs
        for row in rows:
            assert row.labor_cost == Decimal("0")
            assert row.expense_total == Decimal("0")
            assert row.labor_entry_count == 0
            assert row.invoice_count == 0


# ---------------------------------------------------------------------------
# Test: Labor cost calculation
# ---------------------------------------------------------------------------


class TestLaborCostCalculation:
    def test_single_normal_shift_labor_cost(self, summary_session):
        """Single normal shift calculates cost as daily_rate * 1.0."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create labor entry with normal shift
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag.id)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert len(rows) == 1
        assert rows[0].labor_cost == Decimal("100")
        assert rows[0].labor_entry_count == 1

    def test_half_shift_labor_cost(self, summary_session):
        """Half shift calculates cost as daily_rate * 0.5."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create half shift
        _create_labor_entry(summary_session, worker_id, shift_type="half", tag_id=tag.id)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].labor_cost == Decimal("50")

    def test_overtime_shift_labor_cost(self, summary_session):
        """Overtime shift calculates cost as daily_rate * 1.5."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create overtime shift
        _create_labor_entry(summary_session, worker_id, shift_type="overtime", tag_id=tag.id)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].labor_cost == Decimal("150")

    def test_amount_override_replaces_computed_cost(self, summary_session):
        """amount_override replaces the computed shift cost."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create normal shift but override the cost
        _create_labor_entry(
            summary_session,
            worker_id,
            shift_type="normal",
            tag_id=tag.id,
            amount_override=Decimal("75"),
        )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].labor_cost == Decimal("75")

    def test_supplement_only_entry_zero_cost_still_counted(self, summary_session):
        """Supplement-only entry (shift_type=None) has 0 cost but is counted."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create supplement-only entry (no shift)
        _create_labor_entry(summary_session, worker_id, shift_type=None, tag_id=tag.id)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].labor_cost == Decimal("0")
        assert rows[0].labor_entry_count == 1  # Still counted!

    def test_multiple_entries_same_tag_sum_correctly(self, summary_session):
        """Multiple entries tagged with same tag sum to total cost."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create multiple entries (different dates to avoid uniqueness constraint)
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag.id, date_offset_days=0)
        _create_labor_entry(summary_session, worker_id, shift_type="half", tag_id=tag.id, date_offset_days=1)
        _create_labor_entry(summary_session, worker_id, shift_type="overtime", tag_id=tag.id, date_offset_days=2)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        # 100 + 50 + 150 = 300
        assert rows[0].labor_cost == Decimal("300")
        assert rows[0].labor_entry_count == 3


# ---------------------------------------------------------------------------
# Test: Invoice expense calculation
# ---------------------------------------------------------------------------


class TestInvoiceExpenseCalculation:
    def test_single_invoice_single_item_cost(self, summary_session):
        """Single invoice with one item calculates expense as qty * price."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create invoice with one item: qty=2, price=50 = 100 total
        _create_invoice(
            summary_session,
            project_id,
            items=[{"quantity": 2, "unit_price": 50.0}],
            tag_id=tag.id,
        )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].expense_total == Decimal("100")
        assert rows[0].invoice_count == 1

    def test_invoice_multiple_items_sum_correctly(self, summary_session):
        """Invoice with multiple items sums all (qty * price) products."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create invoice with multiple items:
        # item1: 2 * 50 = 100
        # item2: 3 * 75 = 225
        # Total = 325
        _create_invoice(
            summary_session,
            project_id,
            items=[
                {"quantity": 2, "unit_price": 50.0},
                {"quantity": 3, "unit_price": 75.0},
            ],
            tag_id=tag.id,
        )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].expense_total == Decimal("325")

    def test_multiple_invoices_same_tag_sum_correctly(self, summary_session):
        """Multiple invoices with same tag sum to total expense."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(
            project_id=project_id,
            name="TestTag",
            color="#000000",
        )
        tag_repo.add(tag)

        # Create three invoices with different amounts
        inv_items_list = [
            {"quantity": 1, "unit_price": 100.0},
            {"quantity": 2, "unit_price": 50.0},
            {"quantity": 1, "unit_price": 75.0},
        ]
        for inv_items in inv_items_list:
            _create_invoice(
                summary_session,
                project_id,
                items=[inv_items],
                tag_id=tag.id,
            )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        # 100 + 100 + 75 = 275
        assert rows[0].expense_total == Decimal("275")
        assert rows[0].invoice_count == 3


# ---------------------------------------------------------------------------
# Test: Untagged bucket
# ---------------------------------------------------------------------------


class TestUntaggedBucket:
    def test_untagged_labor_entries_in_untagged_bucket(self, summary_session):
        """Labor entries with tag_id=None appear in (untagged) bucket."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)

        # Create untagged labor entry
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=None)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert len(rows) == 1
        assert rows[0].tag_id is None
        assert rows[0].tag_name == "(untagged)"
        assert rows[0].tag_color is None
        assert rows[0].labor_cost == Decimal("100")

    def test_untagged_invoices_in_untagged_bucket(self, summary_session):
        """Invoices with tag_id=None appear in (untagged) bucket."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)

        # Create untagged invoice
        _create_invoice(
            summary_session,
            project_id,
            items=[{"quantity": 1, "unit_price": 50.0}],
            tag_id=None,
        )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].tag_id is None
        assert rows[0].expense_total == Decimal("50")

    def test_untagged_bucket_appears_last(self, summary_session):
        """(untagged) bucket appears after all named tags."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)

        # Create two named tags
        tag1 = ProjectTag.create(project_id=project_id, name="Tag A", color="#000000")
        tag2 = ProjectTag.create(project_id=project_id, name="Tag B", color="#111111")
        tag_repo.add(tag1)
        tag_repo.add(tag2)

        # Create entries for each tag + untagged (different dates)
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag1.id, date_offset_days=0)
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag2.id, date_offset_days=1)
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=None, date_offset_days=2)
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert len(rows) == 3
        assert rows[0].tag_name == "Tag A"
        assert rows[1].tag_name == "Tag B"
        assert rows[2].tag_name == "(untagged)"


# ---------------------------------------------------------------------------
# Test: Mixed labor and expenses per tag
# ---------------------------------------------------------------------------


class TestMixedLaborAndExpenses:
    def test_labor_and_expenses_both_counted_same_tag(self, summary_session):
        """Tag with both labor entries and invoices shows both costs."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag = ProjectTag.create(project_id=project_id, name="TestTag", color="#000000")
        tag_repo.add(tag)

        # Create one labor entry (cost = 100) and one invoice (cost = 50)
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag.id)
        _create_invoice(
            summary_session,
            project_id,
            items=[{"quantity": 1, "unit_price": 50.0}],
            tag_id=tag.id,
        )
        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert rows[0].labor_cost == Decimal("100")
        assert rows[0].expense_total == Decimal("50")
        assert rows[0].labor_entry_count == 1
        assert rows[0].invoice_count == 1

    def test_multiple_tags_independent_costs(self, summary_session):
        """Multiple tags maintain independent cost calculations."""
        project_id, owner_id = _create_project_and_users(summary_session, uuid4())
        daily_rate = Decimal("100")

        worker_id = _create_worker(summary_session, project_id, daily_rate)

        tag_repo = SqlAlchemyProjectTagRepository(summary_session)
        tag1 = ProjectTag.create(project_id=project_id, name="Tag1", color="#FF0000")
        tag2 = ProjectTag.create(project_id=project_id, name="Tag2", color="#00FF00")
        tag_repo.add(tag1)
        tag_repo.add(tag2)

        # Tag1: normal shift (100) + invoice (50) = labor 100, expense 50
        _create_labor_entry(summary_session, worker_id, shift_type="normal", tag_id=tag1.id, date_offset_days=0)
        _create_invoice(
            summary_session,
            project_id,
            items=[{"quantity": 1, "unit_price": 50.0}],
            tag_id=tag1.id,
        )

        # Tag2: half shift (50) + invoice (100) = labor 50, expense 100
        _create_labor_entry(summary_session, worker_id, shift_type="half", tag_id=tag2.id, date_offset_days=1)
        _create_invoice(
            summary_session,
            project_id,
            items=[{"quantity": 2, "unit_price": 50.0}],
            tag_id=tag2.id,
        )

        summary_session.commit()

        membership_reader = SqlAlchemyProjectMembershipReader(summary_session)
        usecase = TagSummaryUseCase(
            tag_repo=tag_repo,
            labor_reader=tag_repo,
            expense_reader=tag_repo,
            membership_reader=membership_reader,
        )

        rows = usecase.execute(actor_id=owner_id, project_id=project_id)
        assert len(rows) == 2

        # Find rows by name
        tag1_row = next((r for r in rows if r.tag_name == "Tag1"), None)
        tag2_row = next((r for r in rows if r.tag_name == "Tag2"), None)

        assert tag1_row is not None
        assert tag2_row is not None
        assert tag1_row.labor_cost == Decimal("100")
        assert tag1_row.expense_total == Decimal("50")
        assert tag2_row.labor_cost == Decimal("50")
        assert tag2_row.expense_total == Decimal("100")
