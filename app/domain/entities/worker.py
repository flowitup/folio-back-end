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

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Worker):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
