"""Labor repository ports."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from app.domain.entities.worker import Worker
from app.domain.entities.labor_entry import LaborEntry


@dataclass
class LaborSummaryRow:
    """Aggregated labor cost per worker."""
    worker_id: UUID
    worker_name: str
    days_worked: int
    total_cost: Decimal


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
    ) -> List[LaborEntry]:
        """List entries for a project with optional filters."""
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
    ) -> List[LaborSummaryRow]:
        """Get aggregated labor summary per worker."""
        ...
