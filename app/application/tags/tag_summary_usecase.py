"""TagSummaryUseCase — per-project cost rollup by tag (labor + expenses)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from app.application.tags.dtos import TagSummaryRow
from app.application.tags.exceptions import NotProjectMemberError
from app.application.tags.ports import (
    ExpenseTotalReaderPort,
    LaborCostReaderPort,
    ProjectMembershipReaderPort,
    ProjectTagRepositoryPort,
)


class TagSummaryUseCase:
    """Produce per-tag cost rollup for a project.

    Algorithm:
    1. Fetch all tags for the project.
    2. Query labor costs grouped by tag_id (via LaborCostReaderPort).
    3. Query expense totals grouped by tag_id (via ExpenseTotalReaderPort).
    4. Merge into TagSummaryRow list — one row per tag + one untagged bucket.

    Authorization: acting user must be a project member.
    """

    def __init__(
        self,
        tag_repo: ProjectTagRepositoryPort,
        labor_reader: LaborCostReaderPort,
        expense_reader: ExpenseTotalReaderPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._tag_repo = tag_repo
        self._labor_reader = labor_reader
        self._expense_reader = expense_reader
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, project_id: UUID) -> list[TagSummaryRow]:
        """Return tag summary rows, tagged rows first (sorted by name), then untagged.

        Raises:
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {project_id}.")

        tags = self._tag_repo.list_by_project(project_id)
        labor_map = self._labor_reader.sum_labor_cost_by_tag(project_id)
        expense_map = self._expense_reader.sum_expense_by_tag(project_id)

        # Collect all tag_ids that appear in either map (including None = untagged)
        all_ids: set[UUID | None] = set(labor_map.keys()) | set(expense_map.keys())
        # Ensure every known tag appears even if it has zero cost
        for t in tags:
            all_ids.add(t.id)

        # Build a lookup: tag_id -> ProjectTag
        tag_lookup = {t.id: t for t in tags}

        rows: list[TagSummaryRow] = []
        untagged_row: TagSummaryRow | None = None

        for tid in all_ids:
            labor_cost, entry_count = labor_map.get(tid, (Decimal("0"), 0))
            expense_total, inv_count = expense_map.get(tid, (Decimal("0"), 0))

            if tid is None:
                untagged_row = TagSummaryRow(
                    tag_id=None,
                    tag_name="(untagged)",
                    tag_color=None,
                    labor_cost=labor_cost,
                    expense_total=expense_total,
                    labor_entry_count=entry_count,
                    invoice_count=inv_count,
                )
            elif tid in tag_lookup:
                t = tag_lookup[tid]
                rows.append(
                    TagSummaryRow(
                        tag_id=t.id,
                        tag_name=t.name,
                        tag_color=t.color,
                        labor_cost=labor_cost,
                        expense_total=expense_total,
                        labor_entry_count=entry_count,
                        invoice_count=inv_count,
                    )
                )
            # If tid is not None and not in tag_lookup it was deleted with SET NULL
            # already handled — the None bucket covers those rows.

        # Named tags sorted alphabetically; untagged appended at the end if present.
        rows.sort(key=lambda r: r.tag_name)
        if untagged_row is not None:
            rows.append(untagged_row)

        return rows
