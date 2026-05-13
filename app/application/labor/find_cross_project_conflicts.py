"""Find cross-project labor conflicts on a given date (Phase 4).

A conflict exists when a Person who has an active Worker on the target
project also has an active Worker on a DIFFERENT project in the SAME
company, with a labor_entry on the requested date.

The endpoint informs — it does not block. The LogDayDialog renders a
⚠ badge on conflicting tiles and gates Save behind a confirm modal;
the bulk-log endpoint re-runs the same check at write time and refuses
without ``acknowledge_conflicts=True``.

Plan: 260512-2341-labor-calendar-and-bulk-log → phase-04 (4a).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional
from uuid import UUID

from app.application.labor.ports import (
    CrossProjectConflict,
    ILaborEntryRepository,
)


@dataclass
class FindCrossProjectConflictsRequest:
    project_id: UUID
    date: date
    person_ids: Optional[List[UUID]] = None


@dataclass
class FindCrossProjectConflictsResponse:
    conflicts: List[CrossProjectConflict] = field(default_factory=list)


class FindCrossProjectConflictsUseCase:
    """Return a list of conflict groups (one per Person)."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._entry_repo = entry_repo

    def execute(
        self, request: FindCrossProjectConflictsRequest
    ) -> FindCrossProjectConflictsResponse:
        conflicts = self._entry_repo.find_cross_project_conflicts(
            project_id=request.project_id,
            date=request.date,
            person_ids=request.person_ids,
        )
        return FindCrossProjectConflictsResponse(conflicts=conflicts)
