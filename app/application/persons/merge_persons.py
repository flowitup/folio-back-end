"""Merge Persons use case.

Reassigns all Worker rows from a *source* Person to a *target* Person, moves
or merges the source's company profiles (company_persons) and its account
link onto the target, then deletes the source. The two are treated as the
same physical human and consolidated into one identity row: nothing the
source had is dropped (company_persons cascade on the source's delete, so
they must be carried over first).

Transactional: the worker reassignment + source delete happen in a single
DB transaction. On any exception the caller's session is left to roll back.

Phase 1c of plan 260512-2341-labor-calendar-and-bulk-log.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.persons.ports import IPersonRepository
from app.infrastructure.database.models import WorkerModel
from app.infrastructure.database.models.company_person import CompanyPersonModel
from app.infrastructure.database.models.person import PersonModel


class PersonNotFoundError(LookupError):
    """Raised when source or target Person does not exist."""


class InvalidMergeError(ValueError):
    """Raised on logically-invalid merge requests (same source/target, etc.)."""


class MergeConflictError(ValueError):
    """Raised when the two persons are linked to different user accounts (HTTP 409)."""


@dataclass
class MergePersonsRequest:
    source_person_id: UUID
    target_person_id: UUID


@dataclass
class MergePersonsResponse:
    target_person_id: str
    workers_reassigned: int
    # Company profiles of the source re-pointed to the target (no profile there yet).
    profiles_moved: int = 0
    # Company profiles merged into the target's profile in the same company.
    profiles_merged: int = 0
    # True when the source's account link was carried over to the target.
    account_linked: bool = False


class MergePersonsUseCase:
    """Consolidate two Person rows into one.

    Side effects (one transaction):
      * UPDATE workers SET person_id = target WHERE person_id = source
      * each source company profile: re-pointed to the target, or merged into the
        target's profile of that company (target values win, blanks are filled)
      * source.user_id / phone copied to the target when it has none
      * DELETE FROM persons WHERE id = source

    Raises MergeConflictError when both persons are linked to different accounts.
    """

    def __init__(self, person_repo: IPersonRepository, db_session: Session):
        self._repo = person_repo
        self._db = db_session

    def _find_target(self, source, target_id: UUID):
        """The target person, refused before anything moves when it has another account."""
        target = self._repo.find_by_id(target_id)
        if (
            target is not None
            and source.user_id is not None
            and target.user_id is not None
            and source.user_id != target.user_id
        ):
            raise MergeConflictError("Both persons are linked to different accounts; they cannot be the same person")
        return target

    def execute(self, request: MergePersonsRequest) -> MergePersonsResponse:
        if request.source_person_id == request.target_person_id:
            raise InvalidMergeError("source and target must differ")

        source = self._repo.find_by_id(request.source_person_id)
        if source is None:
            raise PersonNotFoundError(f"source person {request.source_person_id} not found")

        target = self._find_target(source, request.target_person_id)
        if target is None:
            raise PersonNotFoundError(f"target person {request.target_person_id} not found")

        # Reassign all Worker rows in one UPDATE — much cheaper than
        # loading entities and rewriting them one at a time.
        reassigned = (
            self._db.query(WorkerModel)
            .filter(WorkerModel.person_id == source.id)
            .update({WorkerModel.person_id: target.id}, synchronize_session=False)
        )

        moved, merged = self._carry_company_profiles(source.id, target.id)
        account_linked = self._carry_identity(source.id, target.id)

        # Now source has zero Worker references — safe to delete.
        # The persons.id FK in workers is ON DELETE RESTRICT, so the DB
        # itself protects us if reassignment missed any rows.
        self._repo.delete(source.id)

        # Commit both UPDATE + DELETE atomically.
        self._db.commit()

        return MergePersonsResponse(
            target_person_id=str(target.id),
            workers_reassigned=int(reassigned),
            profiles_moved=moved,
            profiles_merged=merged,
            account_linked=account_linked,
        )

    def _carry_company_profiles(self, source_id: UUID, target_id: UUID) -> tuple[int, int]:
        """Move each source company profile to the target, or merge it into the target's one."""
        moved = merged = 0
        source_rows = self._db.query(CompanyPersonModel).filter(CompanyPersonModel.person_id == source_id).all()
        for row in source_rows:
            existing = (
                self._db.query(CompanyPersonModel)
                .filter(CompanyPersonModel.company_id == row.company_id, CompanyPersonModel.person_id == target_id)
                .one_or_none()
            )
            if existing is None:
                row.person_id = target_id
                moved += 1
                continue
            # Same company on both sides: keep the target's values, fill its blanks.
            fill = {
                "labor_role_id": row.labor_role_id,
                "default_daily_rate": row.default_daily_rate,
                "phone_normalized": row.phone_normalized,
                "pending_company_role": row.pending_company_role,
            }
            # A linked (non-pending) profile on either side makes the merged one linked.
            still_pending = existing.pending_expires_at is not None and row.pending_expires_at is not None
            keep_active = bool(existing.is_active or row.is_active)
            # Delete first: phone_normalized is unique within a company.
            self._db.delete(row)
            self._db.flush()
            for field, value in fill.items():
                if getattr(existing, field) is None and value is not None:
                    setattr(existing, field, value)
            if not still_pending:
                existing.pending_expires_at = None
            existing.is_active = keep_active
            merged += 1
        self._db.flush()
        return moved, merged

    def _carry_identity(self, source_id: UUID, target_id: UUID) -> bool:
        """Copy the account link and phone to the target when it has none."""
        source = self._db.get(PersonModel, source_id)
        target = self._db.get(PersonModel, target_id)
        if source is None or target is None:
            return False
        linked = False
        if target.user_id is None and source.user_id is not None:
            user_id = source.user_id
            source.user_id = None  # persons.user_id is unique
            self._db.flush()
            target.user_id = user_id
            linked = True
        if not target.phone and source.phone:
            target.phone = source.phone
            target.phone_normalized = source.phone_normalized
        self._db.flush()
        return linked
