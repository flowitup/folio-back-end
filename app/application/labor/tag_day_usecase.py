"""Tag-day use case — bulk set/clear tag_id on all labor entries of a project day."""

from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository
from app.application.tags.exceptions import InvalidProjectTagError


@dataclass
class TagDayRequest:
    project_id: UUID
    date: date
    # None clears the tag for every entry of the day; a UUID must belong to
    # this project (validated below) before it is applied.
    tag_id: Optional[UUID]


@dataclass
class TagDayResponse:
    updated_count: int
    date: str
    tag_id: Optional[str]


class TagDayUseCase:
    """Bulk set (or clear) the tag on every labor entry of a project day.

    Overwrite semantics: any existing tag_id on those entries is replaced.
    A day with zero entries is not an error — updated_count is simply 0.
    """

    def __init__(
        self,
        entry_repo: ILaborEntryRepository,
        tag_repo,  # ProjectTagRepositoryPort — required so same-project guard is always active
    ):
        self._repo = entry_repo
        self._tag_repo = tag_repo

    def execute(self, request: TagDayRequest) -> TagDayResponse:
        # Guard: an assigned tag_id must belong to the same project as the
        # target entries — mirrors UpdateAttendanceUseCase's same-project check.
        if request.tag_id is not None:
            tag = self._tag_repo.get_by_id(request.tag_id)
            if tag is None or tag.project_id != request.project_id:
                raise InvalidProjectTagError(f"Tag {request.tag_id} does not belong to this project")

        updated_count = self._repo.set_tag_for_date(
            project_id=request.project_id,
            date=request.date,
            tag_id=request.tag_id,
        )

        return TagDayResponse(
            updated_count=updated_count,
            date=request.date.isoformat(),
            tag_id=str(request.tag_id) if request.tag_id is not None else None,
        )
