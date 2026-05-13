"""Merge Persons use case.

Reassigns all Worker rows from a *source* Person to a *target* Person, then
deletes the source. The two are treated as the same physical human and
consolidated into one identity row.

Transactional: the worker reassignment + source delete happen in a single
DB transaction. On any exception the caller's session is left to roll back.

Phase 1c of plan 260512-2341-labor-calendar-and-bulk-log.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.persons.ports import IPersonRepository
from app.infrastructure.database.models import WorkerModel


class PersonNotFoundError(LookupError):
    """Raised when source or target Person does not exist."""


class InvalidMergeError(ValueError):
    """Raised on logically-invalid merge requests (same source/target, etc.)."""


@dataclass
class MergePersonsRequest:
    source_person_id: UUID
    target_person_id: UUID


@dataclass
class MergePersonsResponse:
    target_person_id: str
    workers_reassigned: int


class MergePersonsUseCase:
    """Consolidate two Person rows into one.

    Side effects:
      * UPDATE workers SET person_id = target WHERE person_id = source
      * DELETE FROM persons WHERE id = source

    Returns the number of Worker rows reassigned.
    """

    def __init__(self, person_repo: IPersonRepository, db_session: Session):
        self._repo = person_repo
        self._db = db_session

    def execute(self, request: MergePersonsRequest) -> MergePersonsResponse:
        if request.source_person_id == request.target_person_id:
            raise InvalidMergeError("source and target must differ")

        source = self._repo.find_by_id(request.source_person_id)
        if source is None:
            raise PersonNotFoundError(
                f"source person {request.source_person_id} not found"
            )

        target = self._repo.find_by_id(request.target_person_id)
        if target is None:
            raise PersonNotFoundError(
                f"target person {request.target_person_id} not found"
            )

        # Reassign all Worker rows in one UPDATE — much cheaper than
        # loading entities and rewriting them one at a time.
        reassigned = (
            self._db.query(WorkerModel)
            .filter(WorkerModel.person_id == source.id)
            .update({WorkerModel.person_id: target.id}, synchronize_session=False)
        )

        # Now source has zero Worker references — safe to delete.
        # The persons.id FK in workers is ON DELETE RESTRICT, so the DB
        # itself protects us if reassignment missed any rows.
        self._repo.delete(source.id)

        # Commit both UPDATE + DELETE atomically.
        self._db.commit()

        return MergePersonsResponse(
            target_person_id=str(target.id),
            workers_reassigned=int(reassigned),
        )
