"""Project domain entity."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID


@dataclass(slots=True)
class Project:
    """
    Project aggregate root.

    Represents a construction project that users can be assigned to.
    """

    id: UUID
    name: str
    owner_id: UUID
    created_at: datetime
    address: Optional[str] = None
    invoice_prefix: Optional[str] = None
    updated_at: Optional[datetime] = None
    user_ids: List[UUID] = field(default_factory=list)
    # Budget tracking — nullable; None means "no budget set".
    budget: Optional[Decimal] = None
    # Free-text source / funding description for the budget (e.g. "Client contract").
    budget_source: Optional[str] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Project):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)
