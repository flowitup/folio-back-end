"""Worker domain entity."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class Worker:
    """
    Worker entity.

    Represents an external worker assigned to a construction project.
    Workers are project-scoped (duplicated per project, not shared).
    """

    id: UUID
    project_id: UUID
    name: str
    daily_rate: Decimal
    created_at: datetime
    phone: Optional[str] = None
    is_active: bool = True
    updated_at: Optional[datetime] = None

    # Joined Person identity — populated by the repository when the FK is
    # set (post Phase 1c backfill). None for unlinked workers; the API
    # layer surfaces this via WorkerResponse.person_*. See plan
    # 260512-2341-labor-calendar-and-bulk-log → phase-01 (cook 1d-ii-a).
    person_id: Optional[UUID] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None

    # Joined LaborRole identity — populated by the repository when role_id is set.
    # None for workers without a role assignment.
    role_id: Optional[UUID] = None
    role_name: Optional[str] = None
    role_color: Optional[str] = None
    # Stable key of a default role (tho_chinh / tho_phu) so clients can translate it.
    role_slug: Optional[str] = None

    # Signed-in account allowed to log its own attendance for this worker.
    # None for the (common) case of a worker without an app account.
    user_id: Optional[UUID] = None

    # Transient — resolved by ListWorkersUseCase from the rate-change timeline.
    # None until the use case populates it; the API falls back to daily_rate.
    current_daily_rate: Optional[Decimal] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Worker):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


def worker_display_name(person_name: Optional[str], worker_name: str) -> str:
    """The name a worker is shown under: their Person's name, else the worker row's.

    A Person created for a phone-only account carries the phone number as its
    name until someone renames it; printing "+33621500003" as the worker in an
    export or a summary helps nobody, so the worker row's own name wins then.
    """
    from app.domain.value_objects.phone_number import is_phone_number

    name = (person_name or "").strip()
    if not name:
        return worker_name
    if is_phone_number(name) and worker_name and not is_phone_number(worker_name):
        return worker_name
    return name
