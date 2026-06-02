"""ProjectTag domain entity — project-scoped phase/tag for grouping labor + expenses."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_MAX_NAME_LEN = 100


def _validate_name(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise ValueError("Tag name must not be empty")
    if len(stripped) > _MAX_NAME_LEN:
        raise ValueError(f"Tag name must not exceed {_MAX_NAME_LEN} characters")
    return stripped


def _validate_color(color: str) -> str:
    if not _HEX_COLOR.match(color):
        raise ValueError("Color must be a valid hex color (#RRGGBB)")
    return color


@dataclass(frozen=True)
class ProjectTag:
    """Immutable project-scoped tag entity.

    A tag (e.g. "Fondations", "Charpente") groups labor entries and invoices
    within a project for cost-rollup reporting. Tags are unique by name within
    a project (enforced at DB level; validated at domain level on create/update).
    """

    id: UUID
    project_id: UUID
    name: str
    color: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        # Validate — raise ValueError for bad inputs.
        # Note: we validate the stored values (post-strip) not raw inputs,
        # because the factory always strips before constructing.
        _validate_name(self.name)
        _validate_color(self.color)

    @classmethod
    def create(
        cls,
        *,
        project_id: UUID,
        name: str,
        color: str,
    ) -> ProjectTag:
        """Create a new ProjectTag with validated, stripped name."""
        now = datetime.now(timezone.utc)
        return cls(
            id=uuid4(),
            project_id=project_id,
            name=_validate_name(name),
            color=_validate_color(color),
            created_at=now,
            updated_at=now,
        )

    def with_updates(
        self,
        *,
        name: Optional[str] = None,
        color: Optional[str] = None,
    ) -> ProjectTag:
        """Return a new ProjectTag with the given fields replaced.

        Only supplied (non-None) arguments are changed; others carry over.
        """
        new_name = _validate_name(name) if name is not None else self.name
        new_color = _validate_color(color) if color is not None else self.color
        return replace(
            self,
            name=new_name,
            color=new_color,
            updated_at=datetime.now(timezone.utc),
        )
