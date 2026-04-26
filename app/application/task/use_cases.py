"""Task use cases for the planning Kanban board."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from app.application.task.ports import ITaskRepository
from app.domain.entities.task import Task, TaskPriority, TaskStatus

# Step size used when appending a new task to the end of a column. Gaps between
# positions allow drop-between-cards updates without renumbering the column.
POSITION_STEP = 1000


class TaskNotFoundError(LookupError):
    """Raised when a task id has no matching record."""


@dataclass
class CreateTaskRequest:
    project_id: UUID
    title: str
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.MEDIUM
    description: Optional[str] = None
    assignee_id: Optional[UUID] = None
    due_date: Optional[date] = None
    labels: list[str] = field(default_factory=list)
    created_by: Optional[UUID] = None


class CreateTaskUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, req: CreateTaskRequest) -> Task:
        if not req.title.strip():
            raise ValueError("Task title is required")
        # Append: position = (current max in this lane) + STEP, leaving room above.
        next_position = self._repo.max_position(req.project_id, req.status) + POSITION_STEP
        now = datetime.now(timezone.utc)
        task = Task(
            id=uuid4(),
            project_id=req.project_id,
            title=req.title.strip(),
            description=req.description,
            status=req.status,
            priority=req.priority,
            position=next_position,
            assignee_id=req.assignee_id,
            due_date=req.due_date,
            labels=list(req.labels),
            created_by=req.created_by,
            created_at=now,
            updated_at=now,
        )
        return self._repo.create(task)


class ListTasksUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, project_id: UUID, status: Optional[TaskStatus] = None) -> list[Task]:
        return self._repo.list_by_project(project_id, status)


class GetTaskUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, task_id: UUID) -> Task:
        task = self._repo.find_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")
        return task


@dataclass
class UpdateTaskRequest:
    """Partial update — only fields that are not None are applied."""

    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TaskPriority] = None
    assignee_id: Optional[UUID] = None  # use sentinel via separate fn if you need to clear
    due_date: Optional[date] = None
    labels: Optional[list[str]] = None


class UpdateTaskUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, task_id: UUID, req: UpdateTaskRequest) -> Task:
        task = self._repo.find_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")
        if req.title is not None:
            if not req.title.strip():
                raise ValueError("Task title cannot be empty")
            task.title = req.title.strip()
        if req.description is not None:
            task.description = req.description
        if req.priority is not None:
            task.priority = req.priority
        if req.assignee_id is not None:
            task.assignee_id = req.assignee_id
        if req.due_date is not None:
            task.due_date = req.due_date
        if req.labels is not None:
            task.labels = list(req.labels)
        return self._repo.update(task)


class MoveTaskUseCase:
    """Drag-drop atomic update: change status and/or position.

    `before_id` / `after_id` express the drop target as neighbours; the use
    case computes a position between them. If only one neighbour is given,
    the new position is bumped up/down by `POSITION_STEP`. If neither is
    given, the task goes to the end of the lane.
    """

    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(
        self,
        task_id: UUID,
        new_status: TaskStatus,
        before_id: Optional[UUID] = None,
        after_id: Optional[UUID] = None,
    ) -> Task:
        task = self._repo.find_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task {task_id} not found")

        before = self._repo.find_by_id(before_id) if before_id else None
        after = self._repo.find_by_id(after_id) if after_id else None

        if before and after:
            # Drop between two cards.
            new_pos = (before.position + after.position) // 2
            if new_pos == before.position:
                # Gap collapsed — nudge target neighbour and rebalance later if needed.
                new_pos = before.position + 1
        elif before:
            new_pos = before.position + POSITION_STEP
        elif after:
            new_pos = max(0, after.position - POSITION_STEP)
        else:
            new_pos = self._repo.max_position(task.project_id, new_status) + POSITION_STEP

        task.status = new_status
        task.position = new_pos
        return self._repo.update(task)


class DeleteTaskUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, task_id: UUID) -> None:
        if not self._repo.delete(task_id):
            raise TaskNotFoundError(f"Task {task_id} not found")
