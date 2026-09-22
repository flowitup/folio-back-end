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

    The drop target is expressed as its neighbours in the destination lane:
    `before_id` is the card that ends up above the moved one, `after_id` the
    card below it. Both are optional — with neither the task lands at the end
    of the lane. The position is always recomputed against the lane as it is
    stored, so a reorder inside the same lane moves the card just as a move
    between lanes does. When no integer fits between the two neighbours the
    lane is renumbered with even steps, because handing out a duplicate
    position would leave the order to the creation date and lose the drop.
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

        # The destination lane without the moved card, ordered by position.
        lane = [t for t in self._repo.list_by_project(task.project_id, new_status) if t.id != task.id]
        index = _drop_index(lane, before_id, after_id)

        above = lane[index - 1].position if index > 0 else None
        below = lane[index].position if index < len(lane) else None

        if above is not None and below is not None:
            new_pos = (above + below) // 2
            needs_renumber = not above < new_pos < below
        elif above is not None:
            new_pos = above + POSITION_STEP
            needs_renumber = False
        elif below is not None:
            new_pos = below - POSITION_STEP
            needs_renumber = new_pos < 0
        else:
            new_pos = POSITION_STEP
            needs_renumber = False

        if needs_renumber:
            new_pos = self._renumber(lane, index)

        task.status = new_status
        task.position = new_pos
        return self._repo.update(task)

    def _renumber(self, lane: list[Task], index: int) -> int:
        """Space the lane out again and return the free slot at *index*."""
        for slot, sibling in enumerate(lane):
            position = (slot if slot < index else slot + 1) * POSITION_STEP + POSITION_STEP
            if sibling.position != position:
                sibling.position = position
                self._repo.update(sibling)
        return index * POSITION_STEP + POSITION_STEP


def _drop_index(lane: list[Task], before_id: Optional[UUID], after_id: Optional[UUID]) -> int:
    """Index the moved card takes in *lane*, from the neighbours the client sent.

    A neighbour that is not in this lane any more (stale client view) is
    ignored; with no usable neighbour the card is appended.
    """
    positions = {t.id: i for i, t in enumerate(lane)}
    if before_id is not None and before_id in positions:
        return positions[before_id] + 1
    if after_id is not None and after_id in positions:
        return positions[after_id]
    return len(lane)


class DeleteTaskUseCase:
    def __init__(self, repo: ITaskRepository) -> None:
        self._repo = repo

    def execute(self, task_id: UUID) -> None:
        if not self._repo.delete(task_id):
            raise TaskNotFoundError(f"Task {task_id} not found")
