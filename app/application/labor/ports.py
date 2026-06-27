"""Labor repository ports."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from app.domain.entities.worker import Worker
from app.domain.entities.labor_entry import LaborEntry
from app.domain.entities.labor_activity import LaborActivity
from app.domain.entities.labor_day_description import LaborDayDescription
from app.domain.entities.worker_rate_change import WorkerRateChange


@dataclass
class LaborSummaryRow:
    """Aggregated labor cost per worker."""

    worker_id: UUID
    worker_name: str
    # SUM of shift_multipliers — full=1.0, half=0.5, overtime=1.5.
    # Decimal so a mix of full + half cleanly produces 2.5 (not 3) and
    # cost / days_worked == daily_rate for non-override rows.
    days_worked: Decimal
    total_cost: Decimal
    banked_hours: int = 0  # sum of supplement_hours for this worker over the period
    daily_rate: Decimal = Decimal(
        "0"
    )  # resolved bonus rate — worker's latest effective rate as of date_to (base-rate fallback); used for bonus-day cost


@dataclass
class MonthlyWorkerSubRow:
    """Per-worker breakdown within a single (year, month) bucket."""

    worker_id: UUID
    worker_name: str
    # Same fractional semantics as LaborSummaryRow.days_worked above.
    days_worked: Decimal
    total_cost: Decimal


@dataclass
class CrossProjectConflictEntry:
    """One other-project entry that conflicts with the target project's
    proposed log on a given date (Phase 4).

    Returned as a sub-row inside CrossProjectConflict.entries — a single
    Person can be active in multiple other projects, so we group by
    Person and list each conflicting entry separately.
    """

    project_id: UUID
    project_name: str
    shift_type: Optional[str]
    supplement_hours: int


@dataclass
class CrossProjectConflict:
    """Grouped conflict description for one Person (Phase 4)."""

    person_id: UUID
    person_name: str
    entries: List["CrossProjectConflictEntry"]


@dataclass
class MonthlyLaborSummaryRow:
    """Aggregated labor cost per (year, month) across every worker on a project.

    Used by the Summary tab to render a year-grouped monthly breakdown when
    no specific month is selected. Each row also carries the per-worker
    sub-rows so the FE can render them inline under the month header
    without an extra round trip.

    Bonus-day cost is intentionally NOT included here — those are derived
    in the per-worker use case from banked_hours, which doesn't roll up
    cleanly into monthly buckets.
    """

    year: int
    month: int
    # SUM of per-worker days_worked for the month — fractional, since
    # individual rows may be half-days. See LaborSummaryRow.days_worked
    # for the multiplier table. Supplement-only rows are excluded.
    total_days: Decimal
    total_cost: Decimal
    workers: List[MonthlyWorkerSubRow]


class IWorkerRepository(ABC):
    """Port for worker persistence operations."""

    @abstractmethod
    def create(self, worker: Worker) -> Worker:
        """Create a new worker. Returns created worker."""
        ...

    @abstractmethod
    def find_by_id(self, worker_id: UUID) -> Optional[Worker]:
        """Find worker by ID. Returns None if not found."""
        ...

    @abstractmethod
    def list_by_project(self, project_id: UUID, active_only: bool = True) -> List[Worker]:
        """List workers for a project."""
        ...

    @abstractmethod
    def update(self, worker: Worker) -> Worker:
        """Update existing worker."""
        ...

    @abstractmethod
    def soft_delete(self, worker_id: UUID) -> bool:
        """Soft delete worker (set is_active=False). Returns True if updated."""
        ...


class ILaborEntryRepository(ABC):
    """Port for labor entry persistence operations."""

    @abstractmethod
    def create(self, entry: LaborEntry) -> LaborEntry:
        """Create a new labor entry. Raises DuplicateEntryError if exists."""
        ...

    @abstractmethod
    def find_by_id(self, entry_id: UUID) -> Optional[LaborEntry]:
        """Find entry by ID. Returns None if not found."""
        ...

    @abstractmethod
    def list_by_project(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        worker_id: Optional[UUID] = None,
        limit: Optional[int] = None,
        tag_id: Optional[UUID] = None,
    ) -> List[LaborEntry]:
        """List entries for a project with optional filters.

        When ``limit`` is set, returns at most that many rows ordered by date
        descending — i.e. the most recent ``limit`` entries.
        When ``tag_id`` is set, returns only entries with that tag_id.
        """
        ...

    @abstractmethod
    def update(self, entry: LaborEntry) -> LaborEntry:
        """Update existing entry."""
        ...

    @abstractmethod
    def delete(self, entry_id: UUID) -> bool:
        """Delete entry. Returns True if deleted."""
        ...

    @abstractmethod
    def get_summary(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        worker_id: Optional[UUID] = None,
    ) -> List[LaborSummaryRow]:
        """Get aggregated labor summary per worker.

        When worker_id is provided, only that worker's rows are returned.
        """
        ...

    @abstractmethod
    def list_by_project_in_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
    ) -> List[LaborEntry]:
        """List all entries for a project within the inclusive date range.

        Ordered by date ASC, then worker_id ASC for deterministic export output.
        """
        ...

    @abstractmethod
    def get_monthly_summary(
        self,
        project_id: UUID,
    ) -> List[MonthlyLaborSummaryRow]:
        """Aggregate labor totals per (year, month) for the whole project.

        Ordered (year DESC, month DESC) — most recent month first.
        """
        ...

    @abstractmethod
    def find_cross_project_conflicts(
        self,
        project_id: UUID,
        date: date,
        person_ids: Optional[List[UUID]] = None,
    ) -> List[CrossProjectConflict]:
        """Find labor entries on ``date`` for Persons that are also active
        on the target ``project_id``, but logged inside *other* projects
        within the same company (Phase 4).

        When ``person_ids`` is provided, results are limited to those
        Persons; otherwise every active worker on the target project is
        considered.

        Ordered by ``person_name`` ASC then ``project_name`` ASC for
        deterministic output. Returns an empty list when no conflicts.
        """
        ...


class IWorkerRateChangeRepository(ABC):
    """Port for worker rate-change persistence operations.

    ``upsert`` inserts or updates by (worker_id, effective_date) so callers
    can use the same endpoint to both create and correct a rate for a given date.
    ``list_by_worker`` / ``list_by_workers`` always return rows ordered
    effective_date DESC so the first match in a scan is the most recent.
    """

    @abstractmethod
    def upsert(self, rc: WorkerRateChange) -> WorkerRateChange:
        """Insert or update the rate change keyed by (worker_id, effective_date)."""
        ...

    @abstractmethod
    def list_by_worker(self, worker_id: UUID) -> List[WorkerRateChange]:
        """Return all rate changes for one worker, effective_date DESC."""
        ...

    @abstractmethod
    def list_by_workers(self, worker_ids: List[UUID]) -> Dict[UUID, List[WorkerRateChange]]:
        """Return rate changes for multiple workers in a single query.

        Keys are worker_id; values are lists ordered effective_date DESC.
        Workers with no rate changes are absent from the dict.
        """
        ...

    @abstractmethod
    def find_by_id(self, rc_id: UUID) -> Optional[WorkerRateChange]:
        """Return the rate change by primary key, or None."""
        ...

    @abstractmethod
    def delete(self, rc_id: UUID) -> bool:
        """Delete the rate change. Returns True if deleted, False if not found."""
        ...


class ILaborActivityRepository(ABC):
    """Port for labor activity persistence operations."""

    @abstractmethod
    def create(self, activity: LaborActivity) -> LaborActivity: ...

    @abstractmethod
    def find_by_id(self, activity_id: UUID) -> Optional[LaborActivity]: ...

    @abstractmethod
    def find_by_project_and_date(self, project_id: UUID, activity_date: date) -> Optional[LaborActivity]:
        """Return the activity for (project_id, date), or None if absent."""
        ...

    @abstractmethod
    def list_by_project(
        self,
        project_id: UUID,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> List[LaborActivity]:
        """List activities for a project, ordered by date DESC then created_at DESC."""
        ...

    @abstractmethod
    def update(self, activity: LaborActivity) -> LaborActivity: ...

    @abstractmethod
    def delete(self, activity_id: UUID) -> bool: ...


class ILaborDayDescriptionRepository(ABC):
    """Port for labor day description persistence operations.

    One description per (project_id, date). Upsert semantics: blank description
    is handled at the use-case layer by calling delete_by_date instead of upsert.
    """

    @abstractmethod
    def find_by_project_and_date(self, project_id: UUID, description_date: date) -> Optional[LaborDayDescription]:
        """Return the description for (project_id, date), or None if absent."""
        ...

    @abstractmethod
    def upsert(self, entity: LaborDayDescription) -> LaborDayDescription:
        """Insert or update the description keyed by (project_id, date).

        If a row exists for (project_id, date), update its description, updated_at.
        Otherwise insert a new row.
        """
        ...

    @abstractmethod
    def list_by_range(
        self,
        project_id: UUID,
        date_from: date,
        date_to: date,
    ) -> List[LaborDayDescription]:
        """Return all descriptions for a project within the inclusive date range.

        Ordered by date ASC for deterministic export output.
        """
        ...

    @abstractmethod
    def delete_by_date(self, project_id: UUID, description_date: date) -> bool:
        """Delete the description for (project_id, date). Returns True if deleted."""
        ...
