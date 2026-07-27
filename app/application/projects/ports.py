"""Project repository port."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from app.domain.entities.project import Project


@dataclass(frozen=True, slots=True)
class ProjectSpent:
    """Spend rollup for a single project, split by who funded it.

    Labor is accrued from attendance entries and settled by labor-type invoices. The
    invoices record *who paid*, not extra cost, so they never add to ``total`` — counting
    both would bill the same work twice. Whatever accrued but has not been settled is
    ``labor_unpaid``: owed to workers, not yet spent by anyone.

    Invariant, asserted by test::

        by_credits + personal + labor_unpaid == total

    ``personal_by_type`` breaks the personal share down by invoice type
    (``materials_services`` | ``others`` | ``refund`` | ``labor``); its values sum to
    ``personal``.
    """

    total: Decimal
    by_credits: Decimal
    personal: Decimal
    labor_accrued: Decimal
    labor_paid: Decimal
    labor_unpaid: Decimal
    personal_by_type: dict[str, Decimal]


class ProjectSpentReaderPort(ABC):
    """Port for reading aggregated project spend (labor + non-released_funds invoices)."""

    @abstractmethod
    def sum_spent_by_projects(self, project_ids: list[UUID]) -> dict[UUID, ProjectSpent]:
        """Return {project_id: ProjectSpent} for each id.

        IDs with no labor entries or qualifying invoices map to zero on both figures.
        Refund invoices carry negative line items and naturally net down the totals.
        """
        ...


class IProjectRepository(ABC):
    """Port for project persistence operations."""

    @abstractmethod
    def create(self, project: Project) -> Project:
        """Create a new project. Returns created project."""
        ...

    @abstractmethod
    def find_by_id(self, project_id: UUID) -> Optional[Project]:
        """Find project by ID. Returns None if not found."""
        ...

    @abstractmethod
    def list_by_user(self, user_id: UUID) -> List[Project]:
        """List projects user is assigned to."""
        ...

    @abstractmethod
    def list_all(self) -> List[Project]:
        """List all projects (admin only)."""
        ...

    @abstractmethod
    def update(self, project: Project) -> Project:
        """Update existing project."""
        ...

    @abstractmethod
    def delete(self, project_id: UUID) -> bool:
        """Delete project. Returns True if deleted."""
        ...

    @abstractmethod
    def add_user(self, project_id: UUID, user_id: UUID) -> None:
        """Assign user to project."""
        ...

    @abstractmethod
    def remove_user(self, project_id: UUID, user_id: UUID) -> None:
        """Remove user from project."""
        ...

    @abstractmethod
    def get_project_users(self, project_id: UUID) -> List[Tuple[UUID, str]]:
        """Get users assigned to a project. Returns list of (id, email) tuples."""
        ...
