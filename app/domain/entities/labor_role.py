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

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Role name must not be empty")
        if not re.match(r"^#[0-9a-fA-F]{6}$", self.color):
            raise ValueError("Color must be a valid hex color (#RRGGBB)")
