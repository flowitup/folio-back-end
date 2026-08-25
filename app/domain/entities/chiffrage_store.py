"""ChiffrageStore domain entity — a shop the project buys from."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class ChiffrageStore:
    """A physical shop the project buys from: where to go, see and buy.

    Declared once per project and referenced by any poste's quotes. Scoping
    these to a poste instead would make "Leroy Merlin" a different record in
    every section, so no basket could ever be totalled across the chantier —
    which is the whole point of comparing shops.

    Free text rather than a link to a bibliothèque supplier: a chain has many
    branches and which one you drive to depends on the chantier, not on the
    company's supplier list. The bibliothèque is also company-scoped, and a
    project may have no company — binding shops to it would break costing for
    exactly those projects.
    """

    id: UUID
    project_id: UUID
    name: str
    address: Optional[str]
    website_url: Optional[str]
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
        address: Optional[str] = None,
        website_url: Optional[str] = None,
    ) -> "ChiffrageStore":
        """Create a new shop at the given position within its project."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            name=name,
            address=address,
            website_url=website_url,
            position=position,
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        name: object = _UNSET,
        address: object = _UNSET,
        website_url: object = _UNSET,
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
            website_url=(self.website_url if website_url is U else website_url),
            updated_at=datetime.now(timezone.utc),
        )

    def with_position(self, position: int) -> "ChiffrageStore":
        """Return a copy moved to a new ordering position within its project."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
