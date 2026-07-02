"""Update attendance use case."""

import dataclasses
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.application.tags.exceptions import InvalidProjectTagError
from app.domain.exceptions.labor_exceptions import LaborEntryNotFoundError


# Sentinel for "field not provided in the PUT body" — distinct from None
# (which means "explicitly clear the field"). Shared by tag_id,
# amount_override, note and shift_type so each nullable field can be
# cleared without dropping the others' patch-if-provided semantics.
_UNSET: object = object()
# Back-compat alias — the API route imports this name.
_TAG_UNSET: object = _UNSET


@dataclass
class UpdateAttendanceRequest:
    entry_id: UUID
    # project_id from the URL path scopes the lookup so callers cannot
    # mutate an entry whose worker belongs to a different project.
    project_id: UUID
    # Nullable fields use the sentinel: _UNSET = not provided (leave as-is),
    # None = clear, value = assign.
    amount_override: object = dataclasses.field(default_factory=lambda: _UNSET)
    note: object = dataclasses.field(default_factory=lambda: _UNSET)
    shift_type: object = dataclasses.field(default_factory=lambda: _UNSET)
    supplement_hours: Optional[int] = None
    tag_id: object = dataclasses.field(default_factory=lambda: _UNSET)


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

        # PATCH semantics via sentinel: a field absent from the PUT body
        # (_UNSET) is left untouched; an explicit null clears it. This is
        # what lets the FE remove an amount override / note / shift after
        # the fact — previously None doubled as "do not touch" and those
        # fields could never be cleared.
        if request.amount_override is not _UNSET:
            entry.amount_override = request.amount_override  # type: ignore[assignment]  # None or Decimal
        if request.note is not _UNSET:
            note = request.note
            entry.note = (note.strip() or None) if isinstance(note, str) else None
        if request.shift_type is not _UNSET:
            entry.shift_type = request.shift_type  # type: ignore[assignment]  # None or str
        if request.supplement_hours is not None:
            entry.supplement_hours = request.supplement_hours
        if request.tag_id is not _UNSET:
            entry.tag_id = request.tag_id  # type: ignore[assignment]  # None or UUID

        # Reject update combinations that would leave the entry invalid —
        # same invariants the create path enforces.
        if entry.shift_type is None and (entry.supplement_hours or 0) == 0:
            raise ValueError("Empty entry: must keep a shift_type or supplement_hours > 0")
        if entry.shift_type is None and entry.amount_override is not None:
            raise ValueError("amount_override requires a shift_type")

        saved = self._repo.update(entry)

        return UpdateAttendanceResponse(
            id=str(saved.id),
            worker_id=str(saved.worker_id),
            date=saved.date.isoformat(),
            amount_override=float(saved.amount_override) if saved.amount_override is not None else None,
            note=saved.note,
            shift_type=saved.shift_type,
            supplement_hours=saved.supplement_hours,
            created_at=saved.created_at.isoformat(),
            tag_id=str(saved.tag_id) if saved.tag_id is not None else None,
        )
