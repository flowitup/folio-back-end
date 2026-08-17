"""ChiffragePoste domain entity — a costing section of a project (e.g. "Lumière")."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffragePoste:
    """Immutable poste entity: a named group of articles to buy within a project.

    Ordering is explicit via ``position`` (see POSITION_STEP in the application
    layer) so the user can reorder postes by drag-and-drop; creation order is
    only the initial default.
    """

    id: UUID
    project_id: UUID
    name: str
    note: Optional[str]
    position: int
    created_at: datetime
    updated_at: datetime

    # Sentinel distinguishing "leave unchanged" (omitted) from an explicit clear.
    _UNSET = object()

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        name: str,
        position: int,
        note: Optional[str] = None,
    ) -> "ChiffragePoste":
        """Create a new poste at the given position."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            name=name,
            note=note,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        name: object = _UNSET,
        note: object = _UNSET,
    ) -> "ChiffragePoste":
        """Return a copy with the given editable fields overwritten.

        Fields left as _UNSET keep their current value; passing None clears the
        field. Position is moved through with_position, never here.
        """
        U = ChiffragePoste._UNSET
        return replace(
            self,
            name=self.name if name is U else name,
            note=self.note if note is U else note,
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffragePoste":
        """Return a copy moved to a new ordering position."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
