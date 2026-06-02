"""Update attendance use case."""

import dataclasses
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.exceptions.labor_exceptions import LaborEntryNotFoundError


# Sentinel for "tag_id not provided in the PUT body" — distinct from None
# (which means "explicitly clear the tag").
_TAG_UNSET: object = object()


@dataclass
class UpdateAttendanceRequest:
    entry_id: UUID
    # project_id from the URL path scopes the lookup so callers cannot
    # mutate an entry whose worker belongs to a different project.
    project_id: UUID
    amount_override: Optional[Decimal] = None
    note: Optional[str] = None
    shift_type: Optional[str] = None
    supplement_hours: Optional[int] = None
    # tag_id uses sentinel: _TAG_UNSET = not provided, None = clear, UUID = assign.
    tag_id: object = dataclasses.field(default_factory=lambda: _TAG_UNSET)


@dataclass
class UpdateAttendanceResponse:
    id: str
    worker_id: str
    date: str
    amount_override: Optional[float]
    note: Optional[str]
    shift_type: Optional[str]
    supplement_hours: int
    created_at: str
    tag_id: Optional[str] = None


class UpdateAttendanceUseCase:
    """Update an existing attendance entry."""

    def __init__(
        self,
        entry_repo: ILaborEntryRepository,
        worker_repo: IWorkerRepository,
        tag_repo,  # ProjectTagRepositoryPort — required so same-project guard is always active
    ):
        self._repo = entry_repo
        self._workers = worker_repo
        self._tag_repo = tag_repo

    def execute(self, request: UpdateAttendanceRequest) -> UpdateAttendanceResponse:
        entry = self._repo.find_by_id(request.entry_id)
        if not entry:
            raise LaborEntryNotFoundError(str(request.entry_id))

        # Cross-project IDOR guard: the entry's worker must belong to the
        # project supplied via the URL path. Mismatch is reported as
        # NotFound to avoid leaking entry existence across tenants.
        worker = self._workers.find_by_id(entry.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise LaborEntryNotFoundError(str(request.entry_id))

        # Guard: when a new tag_id is being assigned (not _TAG_UNSET, not None),
        # it must belong to the same project as the worker.
        if request.tag_id is not _TAG_UNSET and request.tag_id is not None:
            tag_uuid: UUID = request.tag_id  # type: ignore[assignment]
            tag = self._tag_repo.get_by_id(tag_uuid)
            if tag is None or tag.project_id != worker.project_id:
                raise InvalidProjectTagError(f"Tag {request.tag_id} does not belong to this project")

        # All fields use PATCH semantics: None means "do not touch" (except tag_id
        # which uses the _TAG_UNSET sentinel to distinguish "not provided" from "clear").
        if request.amount_override is not None:
            entry.amount_override = request.amount_override
        if request.note is not None:
            entry.note = request.note.strip() or None
        if request.shift_type is not None:
            entry.shift_type = request.shift_type
        if request.supplement_hours is not None:
            entry.supplement_hours = request.supplement_hours
        if request.tag_id is not _TAG_UNSET:
            entry.tag_id = request.tag_id  # type: ignore[assignment]  # None or UUID

        saved = self._repo.update(entry)

        return UpdateAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            date=saved.date.isoformat(),
            amount_override=float(saved.amount_override) if saved.amount_override else None,
            note=saved.note,
            shift_type=saved.shift_type,
            supplement_hours=saved.supplement_hours,
            created_at=saved.created_at.isoformat(),
            tag_id=str(saved.tag_id) if saved.tag_id is not None else None,
        )
