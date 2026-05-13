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
    avatar_url: Optional[str] = None
    is_active: bool = True
    updated_at: Optional[datetime] = None

    # Joined Person identity — populated by the repository when the FK is
    # set (post Phase 1c backfill). None for unlinked workers; the API
    # layer surfaces this via WorkerResponse.person_*. See plan
    # 260512-2341-labor-calendar-and-bulk-log → phase-01 (cook 1d-ii-a).
    person_id: Optional[UUID] = None
    person_name: Optional[str] = None
    person_phone: Optional[str] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Worker):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
