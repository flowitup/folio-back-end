"""Delete attendance use case."""

from dataclasses import dataclass
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository
from app.domain.exceptions.labor_exceptions import LaborEntryNotFoundError


@dataclass
class DeleteAttendanceRequest:
    entry_id: UUID


class DeleteAttendanceUseCase:
    """Delete an attendance entry."""

    def __init__(self, entry_repo: ILaborEntryRepository):
        self._repo = entry_repo

    def execute(self, request: DeleteAttendanceRequest) -> None:
        entry = self._repo.find_by_id(request.entry_id)
        if not entry:
            raise LaborEntryNotFoundError(str(request.entry_id))

        self._repo.delete(request.entry_id)
