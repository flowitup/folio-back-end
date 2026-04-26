"""Task domain entity for the planning Kanban board."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional
from uuid import UUID


class TaskStatus(str, Enum):
    """Kanban lane the task is currently in."""

    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class TaskPriority(str, Enum):
    """Priority for visual ordering and triage."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Task:
    """Domain entity for a planning task."""

    id: UUID
    project_id: UUID
    title: str
    status: TaskStatus
    priority: TaskPriority
    position: int
    created_at: datetime
    updated_at: datetime
    description: Optional[str] = None
    assignee_id: Optional[UUID] = None
    due_date: Optional[date] = None
    labels: list[str] = field(default_factory=list)
    created_by: Optional[UUID] = None
