"""ChiffrageStore domain entity — a shop to visit for a poste's purchases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffrageStore:
    """A physical shop attached to a poste: where to go, see and buy.

    A poste holds several of these — a shopping run for "Lumière" may well mean
    Leroy Merlin for the spots, Point P for the cable and a local shop for the
    fittings, and you want all three addresses in front of you.

    Free text rather than a link to a bibliothèque supplier: a chain has many
    branches and which one you drive to depends on the chantier, not on the
    company's supplier list. The two are related but not the same thing — a
    quote records who sells at what price, a store records where you go.
    """

    id: UUID
    poste_id: UUID
    name: str
    address: Optional[str]
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
        position: int,
        address: Optional[str] = None,
    ) -> "ChiffrageStore":
        """Create a new store entry at the given position within its poste."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            poste_id=poste_id,
            name=name,
            address=address,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        name: object = _UNSET,
        address: object = _UNSET,
    ) -> "ChiffrageStore":
        """Return a copy with the given editable fields overwritten.

        Every optional field must be threaded through here — a field omitted
        from this method silently drops on PATCH.
        """
        U = ChiffrageStore._UNSET
        return replace(
            self,
            name=self.name if name is U else name,
            address=self.address if address is U else address,
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffrageStore":
        """Return a copy moved to a new ordering position within its poste."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
