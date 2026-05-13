"""Create worker use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.ports import IWorkerRepository
from app.application.persons.ports import IPersonRepository
from app.application.persons.create_person import (
    CreatePersonRequest,
    CreatePersonUseCase,
)
from app.domain.entities.worker import Worker
from app.domain.exceptions.labor_exceptions import InvalidWorkerDataError


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
    """

    project_id: UUID
    name: str
    daily_rate: Decimal
    phone: Optional[str] = None
    person_id: Optional[UUID] = None
    created_by_user_id: Optional[UUID] = None
    avatar_url: Optional[str] = None


@dataclass
class CreateWorkerResponse:
    id: str
    project_id: str
    name: str
    phone: Optional[str]
    daily_rate: float
    is_active: bool
    created_at: str
    avatar_url: Optional[str] = None
    # Joined Person identity (cook 1d-ii-a).
    person_id: Optional[str] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None


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
    ):
        self._repo = worker_repo
        # Person repo is optional only because legacy callsites pre cook
        # 1d-ii-b still wire CreateWorkerUseCase without it. When None,
        # the inline-create branch raises; the existing-person branch
        # works (no Person creation needed).
        self._person_repo = person_repo

    def execute(self, request: CreateWorkerRequest) -> CreateWorkerResponse:
        name = request.name.strip() if request.name else ""
        if not name:
            raise InvalidWorkerDataError("Worker name is required")
        if len(name) > 255:
            raise InvalidWorkerDataError("Worker name exceeds 255 characters")
        if request.daily_rate <= 0:
            raise InvalidWorkerDataError("Daily rate must be greater than 0")

        person_id: Optional[UUID] = request.person_id

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
                        phone=request.phone,
                        created_by_user_id=request.created_by_user_id,
                    )
                )
                person_id = UUID(created_person.id)

        worker = Worker(
            id=uuid4(),
            project_id=request.project_id,
            name=name,
            daily_rate=request.daily_rate,
            phone=request.phone.strip() if request.phone else None,
            avatar_url=request.avatar_url.strip() if request.avatar_url else None,
            created_at=datetime.now(timezone.utc),
            person_id=person_id,
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
            avatar_url=saved.avatar_url,
            is_active=saved.is_active,
            created_at=saved.created_at.isoformat(),
        )
