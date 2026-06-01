"""Repository and reader ports (Protocols) for the tags application layer."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.project_tag import ProjectTag

# Re-export shared ports to keep imports consistent across BCs.
from app.application.notes.ports import (  # noqa: F401
    ProjectMembershipReaderPort as ProjectMembershipReaderPort,
    TransactionalSessionPort as TransactionalSessionPort,
)


class ProjectTagRepositoryPort(Protocol):
    """Persistence contract for ProjectTag aggregate."""

    def add(self, tag: ProjectTag) -> None:
        """Insert a new tag."""
        ...

    def get_by_id(self, tag_id: UUID) -> Optional[ProjectTag]:
        """Return tag by UUID, or None if not found."""
        ...

    def list_by_project(self, project_id: UUID) -> list[ProjectTag]:
        """Return all tags for a project ordered by name ASC."""
        ...

    def save(self, tag: ProjectTag) -> None:
        """Persist updated tag fields."""
        ...

    def delete(self, tag_id: UUID) -> None:
        """Delete a tag by UUID. Downstream FK is ON DELETE SET NULL."""
        ...

    def exists_name_in_project(self, project_id: UUID, name: str, exclude_tag_id: Optional[UUID] = None) -> bool:
        """Return True if a tag with this (project_id, name) already exists.

        Pass exclude_tag_id to skip the current tag during update uniqueness checks.
        """
        ...


class LaborCostReaderPort(Protocol):
    """Read port: aggregate effective labor cost per tag for a project.

    Returns a mapping of tag_id → (labor_cost, entry_count).
    None key represents untagged entries.
    """

    def sum_labor_cost_by_tag(self, project_id: UUID) -> dict[Optional[UUID], tuple[Decimal, int]]:
        """
        Return {tag_id: (total_effective_cost, entry_count)} for all labor entries
        in the project. None key = entries with tag_id IS NULL.

        Labor cost = effective_cost() per entry = amount_override if set,
        else daily_rate * shift_multiplier. Supplement-only rows (shift_type NULL)
        contribute 0 cost (not counted).
        """
        ...


class ExpenseTotalReaderPort(Protocol):
    """Read port: aggregate invoice totals per tag for a project.

    Returns a mapping of tag_id → (expense_total, invoice_count).
    None key represents untagged invoices.
    """

    def sum_expense_by_tag(self, project_id: UUID) -> dict[Optional[UUID], tuple[Decimal, int]]:
        """
        Return {tag_id: (total_invoice_amount, invoice_count)} for all invoices
        in the project. None key = invoices with tag_id IS NULL.

        Invoice total is sum of (quantity * unit_price) across all items (JSON).
        """
        ...
