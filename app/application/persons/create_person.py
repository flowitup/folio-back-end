"""Create Person use case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.persons.ports import IPersonRepository
from app.domain.entities.person import Person


class InvalidPersonDataError(ValueError):
    """Raised when input violates basic Person invariants."""


@dataclass
class CreatePersonRequest:
    name: str
    created_by_user_id: UUID
    phone: Optional[str] = None


@dataclass
class CreatePersonResponse:
    id: str
    name: str
    phone: Optional[str]
    normalized_name: str
    created_by_user_id: str
    created_at: str


class CreatePersonUseCase:
    """Create a new Person.

    Phase 1b-ii ships the minimal create path — no dedup against existing
    Persons by name/phone yet. Phase 1c's backfill merge tool handles
    deduplication; until then, callers may produce duplicate rows.
    """

    def __init__(self, person_repo: IPersonRepository):
        self._repo = person_repo

    def execute(self, request: CreatePersonRequest) -> CreatePersonResponse:
        name = (request.name or "").strip()
        if not name:
            raise InvalidPersonDataError("Person name is required")
        if len(name) > 255:
            raise InvalidPersonDataError("Person name exceeds 255 characters")

        phone = (request.phone or "").strip() or None
        if phone and len(phone) > 50:
            raise InvalidPersonDataError("Phone exceeds 50 characters")

        person = Person(
            id=uuid4(),
            name=name,
            normalized_name=Person.normalize(name),
            created_by_user_id=request.created_by_user_id,
            created_at=datetime.now(timezone.utc),
            phone=phone,
        )

        saved = self._repo.create(person)

        return CreatePersonResponse(
            id=str(saved.id),
            name=saved.name,
            phone=saved.phone,
            normalized_name=saved.normalized_name,
            created_by_user_id=str(saved.created_by_user_id),
            created_at=saved.created_at.isoformat(),
        )
