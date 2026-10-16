"""Update worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from app.application.labor.ports import IWorkerRepository
from app.application.labor.role_scope import assert_role_in_project_company
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import (
    WorkerNotFoundError,
    InvalidWorkerDataError,
    InvalidWorkerPhoneError,
    SharedPersonEditError,
)
from app.domain.value_objects.phone_number import InvalidPhoneNumberError, is_phone_number, normalize_phone


_ROLE_SENTINEL = object()


def _same_name(sent: str, *stored: Optional[str]) -> bool:
    """Whether a name sent back is a stored one, ignoring case and spacing (clients re-format it)."""

    def fold(value: str) -> str:
        return " ".join(value.split()).casefold()

    return any(value is not None and fold(value) == fold(sent) for value in stored)


def _same_phone(sent: Optional[str], *stored: Optional[str]) -> bool:
    """Whether a phone sent back is a stored one: equal as typed, or the same number once normalised.

    The mobile form re-sends the E.164 form of the number it shows ("+33612345678"), while the
    stored text is whatever was typed ("06 12 34 56 78"); free-text legacy values only match as typed.
    """
    if sent is None:
        return None in stored
    try:
        sent_normalized: Optional[str] = normalize_phone(sent)
    except InvalidPhoneNumberError:
        sent_normalized = None
    for value in stored:
        if value is None:
            continue
        if value == sent:
            return True
        if sent_normalized is not None:
            try:
                if normalize_phone(value) == sent_normalized:
                    return True
            except InvalidPhoneNumberError:
                pass
    return False


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
    role_slug: Optional[str] = None
    user_id: Optional[str] = None


class UpdateWorkerUseCase:
    """Update an existing worker."""

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        labor_role_repo=None,
        authz_reader=None,
        person_repo=None,
        company_person_repo=None,
    ):
        self._repo = worker_repo
        self._labor_role_repo = labor_role_repo
        self._authz_reader = authz_reader
        self._person_repo = person_repo
        self._company_person_repo = company_person_repo

    def set_person_repo(self, person_repo) -> None:
        """Inject the Person repository; wired after it exists."""
        self._person_repo = person_repo

    def set_company_person_repo(self, company_person_repo: Any) -> None:
        """Inject the company directory; wired after it exists."""
        self._company_person_repo = company_person_repo

    def _person_is_company_private(self, worker: Worker) -> bool:
        """Whether the worker's Person may be renamed or re-phoned for everyone using it.

        True only for an unlinked profile that no other company lists. A Person linked to an
        account belongs to the account holder, and one listed by several companies is shared:
        every screen shows the Person's identity, so a new name or phone for those cannot be
        kept for this company alone and is refused. Fails closed when the directory is not wired.
        """
        if self._person_repo is None or self._company_person_repo is None or self._authz_reader is None:
            return False
        company_id = self._authz_reader.project_company_id(worker.project_id)
        person = self._person_repo.find_by_id(worker.person_id)
        if company_id is None or person is None or person.user_id is not None:
            return False
        profiles = self._company_person_repo.list_for_person(worker.person_id)
        return bool(profiles) and all(profile.company_id == company_id for profile in profiles)

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
            name = request.name.strip()
            # The name belongs to the Person: for a person only this company lists, renaming
            # it here renames them on every project of the company; for any other it is refused.
            if worker.person_id is not None:
                if self._person_is_company_private(worker):
                    self._person_repo.rename(worker.person_id, name, commit=False)
                    worker.person_name = name
                elif not _same_name(name, worker.name, worker.person_name):
                    raise SharedPersonEditError()
            worker.name = name

        if request.phone is not None:
            phone = request.phone.strip() or None
            # A number saved before phones were checked may be free text: sent back unchanged it
            # passes, so the rest of the worker stays editable; a new value must be a phone number.
            if phone and not _same_phone(phone, worker.phone, worker.person_phone) and not is_phone_number(phone):
                raise InvalidWorkerPhoneError()
            # Like the name, the phone moves on the Person only for a person only this company
            # lists; a different number for any other is refused.
            if worker.person_id is not None:
                if self._person_is_company_private(worker):
                    self._person_repo.change_phone(worker.person_id, phone, commit=False)
                    worker.person_phone = phone
                elif not _same_phone(phone, worker.phone, worker.person_phone):
                    raise SharedPersonEditError()
            worker.phone = phone

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
            role_slug=getattr(saved, "role_slug", None),
            user_id=str(saved.user_id) if saved.user_id else None,
        )
