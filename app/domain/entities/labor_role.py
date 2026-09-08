"""LaborRole domain entity."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID


@dataclass(slots=True)
class LaborRole:
    """
    Labor role entity.

    Represents a global labor classification (e.g. "Thợ chính", "Thợ phụ")
    with a display color. Roles are company-global, not project-scoped.
    """

    id: UUID
    name: str
    color: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Phase 2: scopes a role to one company (None = legacy/unscoped row —
    # excluded from a company's list, kept around for ambiguous backfills).
    company_id: Optional[UUID] = None
    # Phase 2: stable i18n key for the two seeded roles ("tho_chinh",
    # "tho_phu") and any future default role — clients key UI copy on this
    # instead of a random per-environment UUID.
    slug: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Role name must not be empty")
        if len(self.name) > 100:
            raise ValueError("Role name must not exceed 100 characters")
        if not re.match(r"^#[0-9a-fA-F]{6}$", self.color):
            raise ValueError("Color must be a valid hex color (#RRGGBB)")
