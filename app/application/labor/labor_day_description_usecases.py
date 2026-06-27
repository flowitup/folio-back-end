"""CRUD use cases for labor day descriptions.

One description per (project_id, date). SetLaborDayDescriptionUseCase upserts:
  - If description.strip() is empty → delete the row and return None.
  - If an entry already exists for the given (project_id, date), update its description.
  - Otherwise create a new entry.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from app.application.labor.ports import ILaborDayDescriptionRepository
from app.domain.entities.labor_day_description import LaborDayDescription


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class LaborDayDescriptionDetail:
    """Output DTO for a single labor day description row."""

    id: UUID
    project_id: UUID
    date: str  # ISO YYYY-MM-DD
    description: str
    created_by: Optional[str]
    created_at: str
    updated_at: str


def _to_detail(d: LaborDayDescription) -> LaborDayDescriptionDetail:
    return LaborDayDescriptionDetail(
        id=d.id,
        project_id=d.project_id,
        date=d.date.isoformat(),
        description=d.description,
        created_by=str(d.created_by) if d.created_by else None,
        created_at=d.created_at.isoformat() if d.created_at else "",
        updated_at=d.updated_at.isoformat() if d.updated_at else "",
    )


# ---------------------------------------------------------------------------
# Request DTOs
# ---------------------------------------------------------------------------


@dataclass
class SetLaborDayDescriptionRequest:
    """Upsert-or-delete request.

    When description.strip() is empty the use-case deletes the existing row
    (if any) and returns None. Otherwise it upserts.
    """

    project_id: UUID
    date: date
    description: str
    created_by: Optional[UUID] = None


@dataclass
class ListLaborDayDescriptionsRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class SetLaborDayDescriptionUseCase:
    """Upsert (or delete) the day's single description entry.

    Empty / blank description triggers deletion of the existing row if one
    is present, maintaining the invariant that every stored row is non-empty.
    Returns LaborDayDescriptionDetail on upsert, or None when cleared.
    """

    def __init__(self, repo: ILaborDayDescriptionRepository) -> None:
        self._repo = repo

    def execute(self, req: SetLaborDayDescriptionRequest) -> Optional[LaborDayDescriptionDetail]:
        stripped = req.description.strip()

        # Blank description → clear row (delete if exists)
        if not stripped:
            self._repo.delete_by_date(req.project_id, req.date)
            return None

        now = datetime.now(timezone.utc)
        existing = self._repo.find_by_project_and_date(req.project_id, req.date)

        if existing is not None:
            # Update in place — preserve original creator and created_at.
            existing.description = stripped
            existing.updated_at = now
            updated = self._repo.upsert(existing)
            return _to_detail(updated)

        entity = LaborDayDescription(
            id=uuid4(),
            project_id=req.project_id,
            date=req.date,
            description=stripped,
            created_by=req.created_by,
            created_at=now,
            updated_at=now,
        )
        created = self._repo.upsert(entity)
        return _to_detail(created)


class ListLaborDayDescriptionsUseCase:
    """List day descriptions for a project within an inclusive date range.

    Returns DTOs sorted by date ASC (stable ordering for PDF export).
    """

    def __init__(self, repo: ILaborDayDescriptionRepository) -> None:
        self._repo = repo

    def execute(self, req: ListLaborDayDescriptionsRequest) -> List[LaborDayDescriptionDetail]:
        descriptions = self._repo.list_by_range(
            project_id=req.project_id,
            date_from=req.date_from,
            date_to=req.date_to,
        )
        # Adapter already orders ASC; sort here for defensive determinism.
        descriptions.sort(key=lambda d: d.date)
        return [_to_detail(d) for d in descriptions]
