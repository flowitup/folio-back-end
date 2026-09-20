"""Warehouse domain entity — a storage place of a company, with the address the crew drives to."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Warehouse:
    """Immutable warehouse entity for the inventory bounded context.

    Company-scoped: two companies may each have a "Main warehouse"; a name is
    not unique across the tenant boundary and not unique inside one either.
    """

    id: UUID
    company_id: UUID
    name: str
    address: Optional[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(cls, *, company_id: UUID, name: str, address: Optional[str] = None) -> "Warehouse":
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            company_id=company_id,
            name=name.strip(),
            address=_clean(address),
            created_at=now,
            updated_at=now,
        )

    # Sentinel so callers can distinguish "leave field unchanged" from an explicit clear.
    _UNSET = object()

    def with_updates(self, *, name: object = _UNSET, address: object = _UNSET) -> "Warehouse":
        """Return a copy with the given fields overwritten; `_UNSET` fields are kept."""
        u = Warehouse._UNSET
        next_name = self.name if name is u else str(name).strip()
        next_address = self.address if address is u else _clean(address)  # type: ignore[arg-type]
        if next_name == self.name and next_address == self.address:
            return self
        return replace(self, name=next_name, address=next_address, updated_at=datetime.now(timezone.utc))


def _clean(value: Optional[str]) -> Optional[str]:
    """Blank strings are stored as NULL so the clients render one "no address" state."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
