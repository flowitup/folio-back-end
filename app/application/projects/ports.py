"""Project repository port."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from app.domain.entities.project import Project


@dataclass(frozen=True, slots=True)
class ProjectSpent:
    """Spend rollup for a single project.

    ``by_credits`` is the subset of ``total`` funded with company money (credit line);
    whatever remains is out-of-pocket personal spend. Labor entries are never
    company-funded, so they only ever contribute to ``total``.
    """

    total: Decimal
    by_credits: Decimal


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
