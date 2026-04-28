"""Tests for labor use cases."""

import pytest
from decimal import Decimal
from datetime import date, datetime, timezone
from uuid import uuid4
from unittest.mock import Mock

from app.domain.entities.worker import Worker
from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    LaborEntryNotFoundError,
    InvalidWorkerDataError,
)
from app.application.labor import (
    CreateWorkerUseCase,
    CreateWorkerRequest,
    UpdateWorkerUseCase,
    UpdateWorkerRequest,
    DeleteWorkerUseCase,
    DeleteWorkerRequest,
    ListWorkersUseCase,
    ListWorkersRequest,
    LogAttendanceUseCase,
    LogAttendanceRequest,
    UpdateAttendanceUseCase,
    UpdateAttendanceRequest,
    DeleteAttendanceUseCase,
    DeleteAttendanceRequest,
    GetLaborSummaryUseCase,
    GetLaborSummaryRequest,
    LaborSummaryRow,
)


@pytest.fixture
def mock_worker_repo():
    return Mock()


@pytest.fixture
def mock_entry_repo():
    return Mock()


@pytest.fixture
def sample_worker():
    return Worker(
        id=uuid4(),
        project_id=uuid4(),
        name="Test Worker",
        daily_rate=Decimal("100.00"),
        phone="+33612345678",
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_entry(sample_worker):
    return LaborEntry(
        id=uuid4(),
        worker_id=sample_worker.id,
        date=date.today(),
        amount_override=None,
        note="Test note",
        shift_type="full",
        created_at=datetime.now(timezone.utc),
    )


class TestCreateWorkerUseCase:
    """Tests for CreateWorkerUseCase."""

    def test_create_worker_success(self, mock_worker_repo, sample_worker):
        mock_worker_repo.create.return_value = sample_worker
        usecase = CreateWorkerUseCase(mock_worker_repo)

        result = usecase.execute(
            CreateWorkerRequest(
                project_id=sample_worker.project_id,
                name="Test Worker",
                daily_rate=Decimal("100.00"),
                phone="+33612345678",
            )
        )

        assert result.name == "Test Worker"
        assert result.daily_rate == 100.0
        mock_worker_repo.create.assert_called_once()

    def test_create_worker_empty_name_raises_error(self, mock_worker_repo):
        usecase = CreateWorkerUseCase(mock_worker_repo)

        with pytest.raises(InvalidWorkerDataError):
            usecase.execute(
                CreateWorkerRequest(
                    project_id=uuid4(),
                    name="   ",
                    daily_rate=Decimal("100.00"),
                )
            )

    def test_create_worker_negative_rate_raises_error(self, mock_worker_repo):
        usecase = CreateWorkerUseCase(mock_worker_repo)

        with pytest.raises(InvalidWorkerDataError):
            usecase.execute(
                CreateWorkerRequest(
                    project_id=uuid4(),
                    name="Worker",
                    daily_rate=Decimal("-10.00"),
                )
            )


class TestUpdateWorkerUseCase:
    """Tests for UpdateWorkerUseCase."""

    def test_update_worker_success(self, mock_worker_repo, sample_worker):
        mock_worker_repo.find_by_id.return_value = sample_worker
        mock_worker_repo.update.return_value = sample_worker
        usecase = UpdateWorkerUseCase(mock_worker_repo)

        result = usecase.execute(
            UpdateWorkerRequest(
                worker_id=sample_worker.id,
                name="Updated Name",
            )
        )

        assert result is not None
        mock_worker_repo.update.assert_called_once()

    def test_update_worker_not_found_raises_error(self, mock_worker_repo):
        mock_worker_repo.find_by_id.return_value = None
        usecase = UpdateWorkerUseCase(mock_worker_repo)

        with pytest.raises(WorkerNotFoundError):
            usecase.execute(
                UpdateWorkerRequest(
                    worker_id=uuid4(),
                    name="New Name",
                )
            )


class TestDeleteWorkerUseCase:
    """Tests for DeleteWorkerUseCase (soft delete)."""

    def test_soft_delete_worker_success(self, mock_worker_repo, sample_worker):
        mock_worker_repo.find_by_id.return_value = sample_worker
        mock_worker_repo.soft_delete.return_value = True
        usecase = DeleteWorkerUseCase(mock_worker_repo)

        usecase.execute(DeleteWorkerRequest(worker_id=sample_worker.id))

        mock_worker_repo.soft_delete.assert_called_once_with(sample_worker.id)

    def test_delete_worker_not_found_raises_error(self, mock_worker_repo):
        mock_worker_repo.find_by_id.return_value = None
        usecase = DeleteWorkerUseCase(mock_worker_repo)

        with pytest.raises(WorkerNotFoundError):
            usecase.execute(DeleteWorkerRequest(worker_id=uuid4()))


class TestLogAttendanceUseCase:
    """Tests for LogAttendanceUseCase."""

    def test_log_attendance_success(self, mock_worker_repo, mock_entry_repo, sample_worker, sample_entry):
        mock_worker_repo.find_by_id.return_value = sample_worker
        mock_entry_repo.create.return_value = sample_entry
        usecase = LogAttendanceUseCase(mock_worker_repo, mock_entry_repo)

        result = usecase.execute(
            LogAttendanceRequest(
                project_id=sample_worker.project_id,
                worker_id=sample_worker.id,
                date=date.today(),
                shift_type="full",
            )
        )

        assert result.worker_id == str(sample_worker.id)
        mock_entry_repo.create.assert_called_once()

    def test_log_attendance_worker_not_found_raises_error(self, mock_worker_repo, mock_entry_repo):
        mock_worker_repo.find_by_id.return_value = None
        usecase = LogAttendanceUseCase(mock_worker_repo, mock_entry_repo)

        with pytest.raises(WorkerNotFoundError):
            usecase.execute(
                LogAttendanceRequest(
                    project_id=uuid4(),
                    worker_id=uuid4(),
                    date=date.today(),
                )
            )

    def test_log_attendance_with_override(self, mock_worker_repo, mock_entry_repo, sample_worker):
        mock_worker_repo.find_by_id.return_value = sample_worker
        entry_with_override = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker.id,
            date=date.today(),
            amount_override=Decimal("150.00"),
            note=None,
            shift_type="full",
            created_at=datetime.now(timezone.utc),
        )
        mock_entry_repo.create.return_value = entry_with_override
        usecase = LogAttendanceUseCase(mock_worker_repo, mock_entry_repo)

        result = usecase.execute(
            LogAttendanceRequest(
                project_id=sample_worker.project_id,
                worker_id=sample_worker.id,
                date=date.today(),
                shift_type="full",
                amount_override=Decimal("150.00"),
            )
        )

        assert result.amount_override == 150.0


class TestUpdateAttendanceUseCase:
    """Tests for UpdateAttendanceUseCase."""

    def test_update_attendance_success(self, mock_entry_repo, sample_entry):
        mock_entry_repo.find_by_id.return_value = sample_entry
        mock_entry_repo.update.return_value = sample_entry
        usecase = UpdateAttendanceUseCase(mock_entry_repo)

        result = usecase.execute(
            UpdateAttendanceRequest(
                entry_id=sample_entry.id,
                note="Updated note",
            )
        )

        assert result is not None
        mock_entry_repo.update.assert_called_once()

    def test_update_attendance_not_found_raises_error(self, mock_entry_repo):
        mock_entry_repo.find_by_id.return_value = None
        usecase = UpdateAttendanceUseCase(mock_entry_repo)

        with pytest.raises(LaborEntryNotFoundError):
            usecase.execute(
                UpdateAttendanceRequest(
                    entry_id=uuid4(),
                    note="New note",
                )
            )


class TestDeleteAttendanceUseCase:
    """Tests for DeleteAttendanceUseCase."""

    def test_delete_attendance_success(self, mock_entry_repo, sample_entry):
        mock_entry_repo.find_by_id.return_value = sample_entry
        mock_entry_repo.delete.return_value = True
        usecase = DeleteAttendanceUseCase(mock_entry_repo)

        usecase.execute(DeleteAttendanceRequest(entry_id=sample_entry.id))

        mock_entry_repo.delete.assert_called_once_with(sample_entry.id)

    def test_delete_attendance_not_found_raises_error(self, mock_entry_repo):
        mock_entry_repo.find_by_id.return_value = None
        usecase = DeleteAttendanceUseCase(mock_entry_repo)

        with pytest.raises(LaborEntryNotFoundError):
            usecase.execute(DeleteAttendanceRequest(entry_id=uuid4()))


class TestListWorkersUseCase:
    """Tests for ListWorkersUseCase."""

    def test_list_workers_returns_active_only(self, mock_worker_repo, sample_worker):
        mock_worker_repo.list_by_project.return_value = [sample_worker]
        usecase = ListWorkersUseCase(mock_worker_repo)

        result = usecase.execute(ListWorkersRequest(project_id=sample_worker.project_id))

        assert len(result) == 1
        assert result[0].name == sample_worker.name

    def test_list_workers_empty_project(self, mock_worker_repo):
        mock_worker_repo.list_by_project.return_value = []
        usecase = ListWorkersUseCase(mock_worker_repo)

        result = usecase.execute(ListWorkersRequest(project_id=uuid4()))

        assert len(result) == 0


class TestGetLaborSummaryUseCase:
    """Tests for GetLaborSummaryUseCase."""

    def test_summary_aggregates_correctly(self, mock_entry_repo):
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker A",
                days_worked=5,
                total_cost=Decimal("500.00"),
            ),
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker B",
                days_worked=3,
                total_cost=Decimal("300.00"),
            ),
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)

        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        assert result.total_days == 8
        assert result.total_cost == 800.0
        assert len(result.rows) == 2

    def test_summary_empty_project(self, mock_entry_repo):
        mock_entry_repo.get_summary.return_value = []
        usecase = GetLaborSummaryUseCase(mock_entry_repo)

        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        assert result.total_days == 0
        assert result.total_cost == 0.0
        assert len(result.rows) == 0
        assert result.total_banked_hours == 0
        assert result.total_bonus_days == 0.0
        assert result.total_bonus_cost == 0.0

    def test_bonus_math_5h_banked(self, mock_entry_repo):
        """5h banked → 0 full days + 1 half day (5 >= 4)."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker A",
                days_worked=3,
                total_cost=Decimal("300.00"),
                banked_hours=5,
                daily_rate=Decimal("100.00"),
            )
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        row = result.rows[0]
        assert row.bonus_full_days == 0
        assert row.bonus_half_days == 1
        assert row.bonus_cost == pytest.approx(50.0)  # 0*100 + 1*100*0.5
        assert result.total_banked_hours == 5
        assert result.total_bonus_days == pytest.approx(0.5)
        assert result.total_bonus_cost == pytest.approx(50.0)

    def test_bonus_math_23h_banked(self, mock_entry_repo):
        """23h banked → 2 full days + 1 half day + 7h discarded (23//8=2, 23%8=7>=4)."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker B",
                days_worked=5,
                total_cost=Decimal("500.00"),
                banked_hours=23,
                daily_rate=Decimal("100.00"),
            )
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        row = result.rows[0]
        assert row.bonus_full_days == 2
        assert row.bonus_half_days == 1
        assert row.bonus_cost == pytest.approx(250.0)  # 2*100 + 1*100*0.5
        assert result.total_bonus_days == pytest.approx(2.5)

    def test_bonus_math_8h_exactly(self, mock_entry_repo):
        """8h banked → 1 full day, 0 half day."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker C",
                days_worked=4,
                total_cost=Decimal("400.00"),
                banked_hours=8,
                daily_rate=Decimal("100.00"),
            )
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        row = result.rows[0]
        assert row.bonus_full_days == 1
        assert row.bonus_half_days == 0
        assert row.bonus_cost == pytest.approx(100.0)
        assert result.total_bonus_days == pytest.approx(1.0)

    def test_bonus_math_3h_discarded(self, mock_entry_repo):
        """3h banked → 0 full + 0 half (remainder < 4 → discarded)."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker D",
                days_worked=2,
                total_cost=Decimal("200.00"),
                banked_hours=3,
                daily_rate=Decimal("100.00"),
            )
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        row = result.rows[0]
        assert row.bonus_full_days == 0
        assert row.bonus_half_days == 0
        assert row.bonus_cost == pytest.approx(0.0)
        assert result.total_bonus_days == pytest.approx(0.0)
        assert result.total_bonus_cost == pytest.approx(0.0)

    def test_bonus_math_zero_banked(self, mock_entry_repo):
        """0h banked → no bonus at all."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Worker E",
                days_worked=5,
                total_cost=Decimal("500.00"),
                banked_hours=0,
                daily_rate=Decimal("100.00"),
            )
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        row = result.rows[0]
        assert row.bonus_full_days == 0
        assert row.bonus_half_days == 0
        assert row.bonus_cost == pytest.approx(0.0)
        assert result.total_bonus_cost == pytest.approx(0.0)

    def test_bonus_math_multi_worker_aggregate(self, mock_entry_repo):
        """Multi-worker: totals are summed correctly."""
        mock_entry_repo.get_summary.return_value = [
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Alpha",
                days_worked=10,
                total_cost=Decimal("1000.00"),
                banked_hours=8,  # 1 full = 100
                daily_rate=Decimal("100.00"),
            ),
            LaborSummaryRow(
                worker_id=uuid4(),
                worker_name="Beta",
                days_worked=5,
                total_cost=Decimal("500.00"),
                banked_hours=5,  # 0 full + 1 half = 50
                daily_rate=Decimal("100.00"),
            ),
        ]
        usecase = GetLaborSummaryUseCase(mock_entry_repo)
        result = usecase.execute(GetLaborSummaryRequest(project_id=uuid4()))

        assert result.total_banked_hours == 13
        assert result.total_bonus_days == pytest.approx(1.5)  # 1.0 + 0.5
        assert result.total_bonus_cost == pytest.approx(150.0)  # 100 + 50


class TestLogAttendanceUseCaseSupplementHours:
    """Tests for supplement_hours threading through LogAttendanceUseCase."""

    def test_supplement_only_entry_accepted(self, mock_worker_repo, mock_entry_repo, sample_worker):
        """shift_type=None + supplement_hours=3 is a valid standalone entry."""
        supplement_entry = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker.id,
            date=date.today(),
            shift_type=None,
            supplement_hours=3,
            created_at=datetime.now(timezone.utc),
        )
        mock_worker_repo.find_by_id.return_value = sample_worker
        mock_entry_repo.create.return_value = supplement_entry
        usecase = LogAttendanceUseCase(mock_worker_repo, mock_entry_repo)

        result = usecase.execute(
            LogAttendanceRequest(
                project_id=sample_worker.project_id,
                worker_id=sample_worker.id,
                date=date.today(),
                shift_type=None,
                supplement_hours=3,
            )
        )

        assert result.shift_type is None
        assert result.supplement_hours == 3

    def test_supplement_hours_threaded_to_entity(self, mock_worker_repo, mock_entry_repo, sample_worker):
        """supplement_hours value is passed through to LaborEntry constructor."""
        saved_entry = LaborEntry(
            id=uuid4(),
            worker_id=sample_worker.id,
            date=date.today(),
            shift_type="full",
            supplement_hours=4,
            created_at=datetime.now(timezone.utc),
        )
        mock_worker_repo.find_by_id.return_value = sample_worker
        mock_entry_repo.create.return_value = saved_entry
        usecase = LogAttendanceUseCase(mock_worker_repo, mock_entry_repo)

        usecase.execute(
            LogAttendanceRequest(
                project_id=sample_worker.project_id,
                worker_id=sample_worker.id,
                date=date.today(),
                shift_type="full",
                supplement_hours=4,
            )
        )

        created_entity = mock_entry_repo.create.call_args[0][0]
        assert created_entity.supplement_hours == 4


class TestUpdateAttendanceUseCaseSupplementHours:
    """Regression tests: PATCH only supplement_hours must persist without dropping other fields."""

    def test_patch_supplement_hours_only(self, mock_entry_repo, sample_entry):
        """Patching only supplement_hours leaves shift_type and note untouched."""
        updated_entry = LaborEntry(
            id=sample_entry.id,
            worker_id=sample_entry.worker_id,
            date=sample_entry.date,
            shift_type=sample_entry.shift_type,
            supplement_hours=6,
            note=sample_entry.note,
            created_at=sample_entry.created_at,
        )
        mock_entry_repo.find_by_id.return_value = sample_entry
        mock_entry_repo.update.return_value = updated_entry
        usecase = UpdateAttendanceUseCase(mock_entry_repo)

        result = usecase.execute(
            UpdateAttendanceRequest(
                entry_id=sample_entry.id,
                supplement_hours=6,
            )
        )

        assert result.supplement_hours == 6
        # Verify entity passed to repo has supplement_hours set
        patched = mock_entry_repo.update.call_args[0][0]
        assert patched.supplement_hours == 6
        # shift_type was NOT in the request → must be unchanged (still "full")
        assert patched.shift_type == "full"

    def test_patch_preserves_shift_type_when_not_in_request(self, mock_entry_repo, sample_entry):
        """When supplement_hours is None (not in PATCH body), existing value is kept."""
        mock_entry_repo.find_by_id.return_value = sample_entry
        mock_entry_repo.update.return_value = sample_entry
        usecase = UpdateAttendanceUseCase(mock_entry_repo)

        usecase.execute(
            UpdateAttendanceRequest(
                entry_id=sample_entry.id,
                note="just updating the note",
            )
        )

        patched = mock_entry_repo.update.call_args[0][0]
        # supplement_hours not in request → entity keeps its original value (0)
        assert patched.supplement_hours == 0
