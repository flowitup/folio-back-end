"""ChiffragePhase domain entity — a stage of the works."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffragePhase:
    """A stage of the works (giai đoạn), declared once for the project.

    A chantier is bought and built stage by stage (gros œuvre, then réseaux,
    then finitions). The same stage cuts across postes: electricity and
    plumbing both have lines for the first fix and lines for the second, so
    the stages live at project level and each article points into them, the
    same way it points at a room.
    """

    id: UUID
    project_id: UUID
    poste_id: UUID
    name: str
    position: int
    created_at: datetime
    updated_at: datetime

    _UNSET = object()

    @classmethod
    def create(cls, *, project_id: UUID, poste_id: UUID, name: str, position: int) -> "ChiffragePhase":
        """Create a new phase at the given position."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            poste_id=poste_id,
            name=name,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(self, *, name: object = _UNSET) -> "ChiffragePhase":
        """Return a copy with the given editable fields overwritten."""
        U = ChiffragePhase._UNSET
        return replace(
            self,
            name=self.name if name is U else name,
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffragePhase":
        """Return a copy moved to a new ordering position."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
