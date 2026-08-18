"""ChiffrageRoom domain entity — a room of the chantier."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffrageRoom:
    """A room (pièce) declared once for the project and reused by every poste.

    The rooms of a chantier are fixed — Salon, Cuisine, Chambre 1 — and the same
    room shows up under Lumière, Peinture and Sol alike. Declaring them per
    poste would mean retyping the same names and drifting on spelling, so the
    vocabulary lives at project level and articles point into it.
    """

    id: UUID
    project_id: UUID
    name: str
    position: int
    created_at: datetime
    updated_at: datetime

    _UNSET = object()

    @classmethod
    def create(cls, *, project_id: UUID, name: str, position: int) -> "ChiffrageRoom":
        """Create a new room at the given position."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            name=name,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(self, *, name: object = _UNSET) -> "ChiffrageRoom":
        """Return a copy with the given editable fields overwritten."""
        U = ChiffrageRoom._UNSET
        return replace(
            self,
            name=self.name if name is U else name,
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffrageRoom":
        """Return a copy moved to a new ordering position."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
