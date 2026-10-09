"""Update worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.application.labor.role_scope import assert_role_in_project_company
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    InvalidWorkerDataError,
    InvalidWorkerPhoneError,
)
from app.domain.value_objects.phone_number import is_phone_number


_ROLE_SENTINEL = object()


@dataclass
class UpdateWorkerRequest:
    worker_id: UUID
    # The project the caller was authorised for; a worker of another project is "not found".
    project_id: UUID
    name: Optional[str] = None
    phone: Optional[str] = None
    # daily_rate is intentionally absent: base rate is immutable after creation.
    # Use the rate-change timeline (POST /workers/<id>/rate-changes) to record
    # pay increases or decreases with an effective date.
    # Use a sentinel so callers can explicitly clear the role assignment
    # (role_id=None means "clear"; omit the field to leave unchanged).
    role_id: object = _ROLE_SENTINEL
    # Same sentinel semantics: None unlinks the app account, omit to leave unchanged.
    user_id: object = _ROLE_SENTINEL
    # True turns a deactivated worker back on (no-op for an active one).
    reactivate: bool = False


@dataclass
class UpdateWorkerResponse:
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str
    # Joined Person identity (cook 1d-ii-a).
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None
    # Joined LaborRole identity.
    role_id: Optional[str] = None
    role_name: Optional[str] = None
    role_color: Optional[str] = None
    user_id: Optional[str] = None


class UpdateWorkerUseCase:
    """Update an existing worker."""

    def __init__(self, worker_repo: IWorkerRepository, labor_role_repo=None, authz_reader=None, person_repo=None):
        self._repo = worker_repo
        self._labor_role_repo = labor_role_repo
        self._authz_reader = authz_reader
        self._person_repo = person_repo

    def set_person_repo(self, person_repo) -> None:
        """Inject the Person repository; wired after it exists."""
        self._person_repo = person_repo

    def set_role_scope(self, labor_role_repo, authz_reader) -> None:
        """Inject what the role check needs; wired after the labor-role repository exists."""
        self._labor_role_repo = labor_role_repo
        self._authz_reader = authz_reader

    def execute(self, request: UpdateWorkerRequest) -> UpdateWorkerResponse:
        worker = self._repo.find_by_id(request.worker_id)
        if worker is None or worker.project_id != request.project_id:
            raise WorkerNotFoundError(str(request.worker_id))

        if request.name is not None:
            if len(request.name.strip()) == 0:
                raise InvalidWorkerDataError("Worker name cannot be empty")
            if len(request.name) > 255:
                raise InvalidWorkerDataError("Worker name exceeds 255 characters")
            worker.name = request.name.strip()
            # The name belongs to the shared Person: renaming it here renames the
            # person in every company and project that uses them.
            if worker.person_id is not None and self._person_repo is not None:
                self._person_repo.rename(worker.person_id, worker.name, commit=False)
                worker.person_name = worker.name

        if request.phone is not None:
            phone = request.phone.strip() or None
            # A number saved before phones were checked may be free text: sent back unchanged it
            # passes, so the rest of the worker stays editable; a new value must be a phone number.
            if phone and phone not in (worker.phone, worker.person_phone) and not is_phone_number(phone):
                raise InvalidWorkerPhoneError()
            worker.phone = phone
            # Like the name, the phone belongs to the shared Person: changing it here changes
            # it in every company and project that uses them.
            if worker.person_id is not None and self._person_repo is not None:
                self._person_repo.change_phone(worker.person_id, worker.phone, commit=False)
                worker.person_phone = worker.phone

        if request.role_id is not _ROLE_SENTINEL:
            if request.role_id != worker.role_id:
                assert_role_in_project_company(
                    self._labor_role_repo, self._authz_reader, request.role_id, worker.project_id  # type: ignore[arg-type]
                )
            worker.role_id = request.role_id  # type: ignore[assignment]

        if request.user_id is not _ROLE_SENTINEL:
            if request.user_id is not None and request.user_id != worker.user_id:
                linked = self._repo.find_by_project_and_user(worker.project_id, request.user_id)  # type: ignore[arg-type]
                if linked is not None and linked.id != worker.id:
                    raise InvalidWorkerDataError("This account is already linked to another worker on this project")
            worker.user_id = request.user_id  # type: ignore[assignment]

        if request.reactivate:
            worker.is_active = True

        worker.updated_at = datetime.now(timezone.utc)
        saved = self._repo.update(worker)

        return UpdateWorkerResponse(
            id=str(saved.id),
            project_id=str(saved.project_id),
            name=saved.name,
            phone=saved.phone,
            daily_rate=float(saved.daily_rate),
            is_active=saved.is_active,
            created_at=saved.created_at.isoformat(),
            person_id=str(saved.person_id) if saved.person_id else None,
            person_name=saved.person_name,
            person_phone=saved.person_phone,
            role_id=str(saved.role_id) if saved.role_id else None,
            role_name=saved.role_name,
            role_color=saved.role_color,
            user_id=str(saved.user_id) if saved.user_id else None,
        )
