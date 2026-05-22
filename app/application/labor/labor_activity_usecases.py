"""CRUD use cases for labor activities."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID, uuid4

from app.application.labor.ports import ILaborActivityRepository
from app.domain.entities.labor_activity import LaborActivity
from app.domain.exceptions.labor_exceptions import LaborActivityNotFoundError


# ---------------------------------------------------------------------------
# Request / Response DTOs
# ---------------------------------------------------------------------------

@dataclass
class CreateLaborActivityRequest:
    project_id: UUID
    date: date
    title: str
    description: Optional[str] = None
    created_by: Optional[UUID] = None


@dataclass
class CreateLaborActivityResponse:
    id: UUID
    project_id: UUID
    date: str
    title: str
    description: Optional[str]
    created_by: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class UpdateLaborActivityRequest:
    activity_id: UUID
    title: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ListLaborActivitiesRequest:
    project_id: UUID
    date_from: Optional[date] = None
    date_to: Optional[date] = None


@dataclass
class LaborActivityDetail:
    id: UUID
    project_id: UUID
    date: str
    title: str
    description: Optional[str]
    created_by: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class DeleteLaborActivityRequest:
    activity_id: UUID


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------

def _to_detail(a: LaborActivity) -> LaborActivityDetail:
    return LaborActivityDetail(
        id=a.id,
        project_id=a.project_id,
        date=a.date.isoformat(),
        title=a.title,
        description=a.description,
        created_by=str(a.created_by) if a.created_by else None,
        created_at=a.created_at.isoformat() if a.created_at else "",
        updated_at=a.updated_at.isoformat() if a.updated_at else "",
    )


class CreateLaborActivityUseCase:
    def __init__(self, repo: ILaborActivityRepository):
        self._repo = repo

    def execute(self, req: CreateLaborActivityRequest) -> LaborActivityDetail:
        now = datetime.now(timezone.utc)
        activity = LaborActivity(
            id=uuid4(),
            project_id=req.project_id,
            date=req.date,
            title=req.title.strip(),
            description=req.description.strip() if req.description else None,
            created_by=req.created_by,
            created_at=now,
            updated_at=now,
        )
        created = self._repo.create(activity)
        return _to_detail(created)


class ListLaborActivitiesUseCase:
    def __init__(self, repo: ILaborActivityRepository):
        self._repo = repo

    def execute(self, req: ListLaborActivitiesRequest) -> List[LaborActivityDetail]:
        activities = self._repo.list_by_project(
            project_id=req.project_id,
            date_from=req.date_from,
            date_to=req.date_to,
        )
        return [_to_detail(a) for a in activities]


class UpdateLaborActivityUseCase:
    def __init__(self, repo: ILaborActivityRepository):
        self._repo = repo

    def execute(self, req: UpdateLaborActivityRequest) -> LaborActivityDetail:
        activity = self._repo.find_by_id(req.activity_id)
        if not activity:
            raise LaborActivityNotFoundError(req.activity_id)
        if req.title is not None:
            activity.title = req.title.strip()
        if req.description is not None:
            activity.description = req.description.strip() if req.description else None
        activity.updated_at = datetime.now(timezone.utc)
        updated = self._repo.update(activity)
        return _to_detail(updated)


class DeleteLaborActivityUseCase:
    def __init__(self, repo: ILaborActivityRepository):
        self._repo = repo

    def execute(self, req: DeleteLaborActivityRequest) -> None:
        if not self._repo.delete(req.activity_id):
            raise LaborActivityNotFoundError(req.activity_id)
