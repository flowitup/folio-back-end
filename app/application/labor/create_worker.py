"""Create worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository
from app.application.persons.ports import IPersonRepository
from app.application.persons.create_person import (
    CreatePersonRequest,
    CreatePersonUseCase,
)
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import InvalidWorkerDataError, WorkerAlreadyLinkedError

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort
    from app.application.company_persons.ports import CompanyPersonRepositoryPort


@dataclass
class CreateWorkerRequest:
    """Application-layer request for creating a Worker.

    ``person_id`` (cook 1d-ii-b) — when set, link this Worker to an
    existing Person; ``name`` is still honored for the workers.name
    legacy column (will be dropped in a later release once FE callers
    read from person_name exclusively).

    ``created_by_user_id`` (cook 1d-ii-b) — required when person_id is
    NOT provided, so the inline Person creation knows who owns the new
    identity. The route layer pulls this from the JWT subject.

    Phase 2 onboarding ("workers from person"): when `person_id` refers to
    a person with a `company_persons` row in this project's company, `name`
    /`phone` fall back to the Person's identity, `daily_rate` falls back to
    `company_persons.default_daily_rate`, and `user_id` falls back to
    `persons.user_id` — each only when the request omits it. `name` becomes
    optional at this layer for that reason; still required overall (raises
    if it cannot be resolved from either source).
    """

    project_id: UUID
    name: Optional[str] = None
    daily_rate: Optional[Decimal] = None
    phone: Optional[str] = None
    person_id: Optional[UUID] = None
    created_by_user_id: Optional[UUID] = None
    role_id: Optional[UUID] = None
    # App account allowed to self-log attendance for this worker.
    user_id: Optional[UUID] = None


@dataclass
class CreateWorkerResponse:
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


class CreateWorkerUseCase:
    """Create a new worker for a project.

    Two flows:

      1. ``person_id`` set → link to that existing Person; skip Person
         create. Caller (the FE PersonTypeahead) has already picked
         an existing identity.

      2. ``person_id`` None → create a fresh Person inline using
         ``name``/``phone``, then link. ``created_by_user_id`` is
         required in this branch (taken from the JWT subject by the
         route).

    Either way the saved Worker comes back with person_id populated.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        person_repo: Optional[IPersonRepository] = None,
        company_person_repo: "Optional[CompanyPersonRepositoryPort]" = None,
        authz_reader: "Optional[AuthzReaderPort]" = None,
    ):
        self._repo = worker_repo
        # Person repo is optional only because legacy callsites pre cook
        # 1d-ii-b still wire CreateWorkerUseCase without it. When None,
        # the inline-create branch raises; the existing-person branch
        # works (no Person creation needed).
        self._person_repo = person_repo
        # Phase 2 onboarding ("workers from person"): both optional so
        # existing callers/tests without the companies BC keep working —
        # the defaulting-from-person-profile branch below is then skipped.
        self._company_person_repo = company_person_repo
        self._authz_reader = authz_reader

    def execute(self, request: CreateWorkerRequest) -> CreateWorkerResponse:
        name = request.name.strip() if request.name else ""
        phone = request.phone
        daily_rate = request.daily_rate
        user_id = request.user_id
        person_id: Optional[UUID] = request.person_id

        # Phase 2 onboarding: fill in whatever the request omitted from the
        # person's company profile — never overrides an explicit value.
        company_person = None
        if person_id is not None and self._company_person_repo is not None and self._authz_reader is not None:
            company_id = self._authz_reader.project_company_id(request.project_id)
            if company_id is not None:
                company_person = self._company_person_repo.find(company_id, person_id)

        if company_person is not None:
            linked_person = self._person_repo.find_by_id(person_id) if self._person_repo is not None else None
            if linked_person is not None:
                name = name or linked_person.name
                phone = phone or linked_person.phone
                if user_id is None:
                    user_id = linked_person.user_id
            if daily_rate is None and company_person.default_daily_rate is not None:
                daily_rate = company_person.default_daily_rate

        if not name:
            raise InvalidWorkerDataError("Worker name is required")
        if len(name) > 255:
            raise InvalidWorkerDataError("Worker name exceeds 255 characters")
        if daily_rate is None or daily_rate <= 0:
            raise InvalidWorkerDataError("Daily rate must be greater than 0")

        if user_id is not None:
            linked = self._repo.find_by_project_and_user(request.project_id, user_id)
            if linked is not None:
                if request.user_id is not None:
                    # Explicit user_id in the request body — a client input error (400).
                    raise InvalidWorkerDataError("This account is already linked to another worker on this project")
                # user_id was derived from person_id — a state conflict the
                # caller could not have known from the request alone (409).
                raise WorkerAlreadyLinkedError(str(request.project_id), str(user_id))

        # Inline-create branch: no person_id → make a Person owned by
        # the caller, then link.
        if person_id is None:
            if self._person_repo is None or request.created_by_user_id is None:
                # Legacy path: skip Person creation entirely. The
                # backfill script will link this Worker later. This keeps
                # older tests that wire CreateWorkerUseCase with the
                # worker_repo only from breaking.
                pass
            else:
                create_person = CreatePersonUseCase(self._person_repo)
                created_person = create_person.execute(
                    CreatePersonRequest(
                        name=name,
                        phone=phone,
                        created_by_user_id=request.created_by_user_id,
                    )
                )
                person_id = UUID(created_person.id)

        worker = Worker(
            id=uuid4(),
            project_id=request.project_id,
            name=name,
            daily_rate=daily_rate,
            phone=phone.strip() if phone else None,
            created_at=datetime.now(timezone.utc),
            person_id=person_id,
            role_id=request.role_id,
            user_id=user_id,
        )

        saved = self._repo.create(worker)

        return CreateWorkerResponse(
            id=str(saved.id),
            project_id=str(saved.project_id),
            person_id=str(saved.person_id) if saved.person_id else None,
            person_name=saved.person_name,
            person_phone=saved.person_phone,
            name=saved.name,
            phone=saved.phone,
            daily_rate=float(saved.daily_rate),
            is_active=saved.is_active,
            created_at=saved.created_at.isoformat(),
            role_id=str(saved.role_id) if saved.role_id else None,
            role_name=saved.role_name,
            role_color=saved.role_color,
            user_id=str(saved.user_id) if saved.user_id else None,
        )
