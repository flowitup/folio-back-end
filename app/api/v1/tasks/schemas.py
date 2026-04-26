"""Pydantic schemas for task (Kanban) API."""

from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

VALID_STATUSES = {"backlog", "todo", "in_progress", "blocked", "done"}
VALID_PRIORITIES = {"low", "medium", "high", "urgent"}


class CreateTaskSchema(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    status: str = Field(default="backlog", pattern="^(backlog|todo|in_progress|blocked|done)$")
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    assignee_id: Optional[UUID] = None
    due_date: Optional[date] = None
    labels: list[str] = Field(default_factory=list)


class UpdateTaskSchema(BaseModel):
    """All fields optional — partial update."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|urgent)$")
    assignee_id: Optional[UUID] = None
    due_date: Optional[date] = None
    labels: Optional[list[str]] = None


class MoveTaskSchema(BaseModel):
    """Atomic drag-drop endpoint payload."""

    status: str = Field(pattern="^(backlog|todo|in_progress|blocked|done)$")
    before_id: Optional[UUID] = None
    after_id: Optional[UUID] = None
