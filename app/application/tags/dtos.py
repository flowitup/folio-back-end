"""Data Transfer Objects for tags use-case results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.domain.entities.project_tag import ProjectTag


@dataclass(frozen=True)
class ProjectTagDto:
    """Read model returned by tag use-cases."""

    id: UUID
    project_id: UUID
    name: str
    color: str
    created_at: datetime
    updated_at: Optional[datetime]

    @classmethod
    def from_entity(cls, tag: ProjectTag) -> ProjectTagDto:
        return cls(
            id=tag.id,
            project_id=tag.project_id,
            name=tag.name,
            color=tag.color,
            created_at=tag.created_at,
            updated_at=tag.updated_at,
        )


@dataclass(frozen=True)
class CreateTagDto:
    """Input DTO for creating a tag."""

    project_id: UUID
    actor_id: UUID
    name: str
    color: str


@dataclass(frozen=True)
class UpdateTagDto:
    """Input DTO for updating a tag (all fields optional)."""

    tag_id: UUID
    project_id: UUID
    actor_id: UUID
    name: Optional[str] = None
    color: Optional[str] = None


@dataclass(frozen=True)
class TagSummaryRow:
    """Per-tag cost rollup row for the summary use-case.

    An untagged bucket is returned with tag_id=None, tag_name='(untagged)',
    tag_color=None when any entries/invoices have no tag assignment.
    """

    tag_id: Optional[UUID]
    tag_name: str
    tag_color: Optional[str]
    labor_cost: Decimal
    expense_total: Decimal
    labor_entry_count: int
    invoice_count: int
