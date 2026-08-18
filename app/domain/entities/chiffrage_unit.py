"""ChiffrageUnit domain entity — a project-scoped custom unit of measure."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffrageUnit:
    """A unit of measure the user added on top of the preset list.

    Scoped to a project rather than a company because ``projects.company_id`` is
    still nullable — company scoping would leave unattached projects with nowhere
    to store a unit. Articles store the symbol as text, so deleting a unit here
    never breaks an article that already uses it.
    """

    id: UUID
    project_id: UUID
    symbol: str
    created_at: datetime

    @classmethod
    def create(cls, *, project_id: UUID, symbol: str) -> "ChiffrageUnit":
        """Create a new custom unit for a project."""
        return cls(
            id=uuid4(),
            project_id=project_id,
            symbol=symbol,
            created_at=datetime.now(timezone.utc),
        )
