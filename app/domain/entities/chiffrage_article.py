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
    # The room this item is for; None until one is picked (the UI requires it,
    # the column stays nullable so pre-existing lines remain valid).
    room_id: Optional[UUID]
    # S3 key of the article's own photo; None when it has none (the UI may
    # still fall back to a linked library product's image).
    image_storage_key: Optional[str]
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
        room_id: Optional[UUID] = None,
        image_storage_key: Optional[str] = None,
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
            room_id=room_id,
            image_storage_key=image_storage_key,
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
        room_id: object = _UNSET,
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
            room_id=self.room_id if room_id is U else room_id,
            updated_at=datetime.now(timezone.utc),
        )

    def with_image_key(self, image_storage_key: Optional[str]) -> "ChiffrageArticle":
        """Attach or clear the article's own photo.

        Deliberately separate from with_updates: the image travels as bytes
        through its own endpoints, so it must not ride on a JSON PATCH.
        """
        return replace(self, image_storage_key=image_storage_key, updated_at=datetime.now(timezone.utc))

    def with_position(self, position: int) -> "ChiffrageArticle":
        """Return a copy moved to a new ordering position within its poste."""
        return replace(self, position=position, updated_at=datetime.now(timezone.utc))
