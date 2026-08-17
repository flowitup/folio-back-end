"""ChiffrageArticle domain entity — one line item to buy inside a poste."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffrageArticle:
    """Immutable article entity: what to buy, how much of it, in which unit.

    ``unit`` is a snapshot symbol (e.g. "u", "m²"), NOT a foreign key to
    ChiffrageUnit. The allowed set is validated at the application boundary;
    storing the text keeps existing articles intact when a custom unit is
    deleted.
    """

    id: UUID
    poste_id: UUID
    name: str
    quantity: Decimal
    unit: Optional[str]
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
        poste_id: UUID,
        name: str,
        quantity: Decimal,
        position: int,
        unit: Optional[str] = None,
        note: Optional[str] = None,
    ) -> "ChiffrageArticle":
        """Create a new article at the given position within its poste."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            poste_id=poste_id,
            name=name,
            quantity=quantity,
            unit=unit,
            note=note,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        name: object = _UNSET,
        quantity: object = _UNSET,
        unit: object = _UNSET,
        note: object = _UNSET,
    ) -> "ChiffrageArticle":
        """Return a copy with the given editable fields overwritten.

        Every optional field must be threaded through here — a field omitted
        from this method silently drops on PATCH.
        """
        U = ChiffrageArticle._UNSET
        return replace(
            self,
            name=self.name if name is U else name,
            quantity=self.quantity if quantity is U else quantity,
            unit=self.unit if unit is U else unit,
            note=self.note if note is U else note,
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffrageArticle":
        """Return a copy moved to a new ordering position within its poste."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
