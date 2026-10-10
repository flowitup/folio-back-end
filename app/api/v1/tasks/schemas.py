"""Pydantic schemas for task (Kanban) API."""

from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from app.api.v1.date_bounds import BusinessDate

VALID_STATUSES = {"backlog", "todo", "in_progress", "blocked", "done"}
VALID_PRIORITIES = {"low", "medium", "high", "urgent"}

# A label is a short tag drawn on the card; a description is free text. Both were unbounded,
# so one long label overflowed the card and the lane.
MAX_DESCRIPTION_LENGTH = 5000
MAX_LABELS = 20
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


def _unique_labels(labels: list[str]) -> list[str]:
    """Labels are a set: keep the first spelling of each, ignoring case, in the order sent."""
    seen: set[str] = set()
    unique = []
    for label in labels:
        key = label.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(label)
    return unique


Labels = Annotated[list[Label], AfterValidator(_unique_labels)]


class CreateTaskSchema(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    status: str = Field(default="backlog", pattern="^(backlog|todo|in_progress|blocked|done)$")
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    assignee_id: Optional[UUID] = None
    due_date: Optional[BusinessDate] = None
    labels: Labels = Field(default_factory=list, max_length=MAX_LABELS)


class UpdateTaskSchema(BaseModel):
    """All fields optional — partial update."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_LENGTH)
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|urgent)$")
    assignee_id: Optional[UUID] = None
    due_date: Optional[BusinessDate] = None
    labels: Optional[Labels] = Field(default=None, max_length=MAX_LABELS)


class MoveTaskSchema(BaseModel):
    """Atomic drag-drop endpoint payload.

    The neighbours describe the gap the card is dropped into, in the lane named
    by `status` — the same lane the card already sits in when it is only
    reordered. Sending neither appends the card to the end of that lane.
    """

    status: str = Field(pattern="^(backlog|todo|in_progress|blocked|done)$")
    before_id: Optional[UUID] = Field(
        default=None, description="Task that ends up directly ABOVE the moved one; null when dropped at the top."
    )
    after_id: Optional[UUID] = Field(
        default=None, description="Task that ends up directly BELOW the moved one; null when dropped at the end."
    )
