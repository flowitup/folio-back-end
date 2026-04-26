"""SQLAlchemy adapter for the task (Kanban planning) repository."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import asc, func
from sqlalchemy.orm import Session

from app.application.task.ports import ITaskRepository
from app.domain.entities.task import Task, TaskPriority, TaskStatus
from app.infrastructure.database.models.task import TaskModel


def _to_entity(m: TaskModel) -> Task:
    return Task(
        id=m.id,
        project_id=m.project_id,
        title=m.title,
        description=m.description,
        status=TaskStatus(m.status),
        priority=TaskPriority(m.priority),
        position=m.position,
        assignee_id=m.assignee_id,
        due_date=m.due_date,
        labels=list(m.labels or []),
        created_by=m.created_by,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


class SQLAlchemyTaskRepository(ITaskRepository):
    """Persistence adapter for Kanban tasks."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, task: Task) -> Task:
        model = TaskModel(
            id=task.id,
            project_id=task.project_id,
            title=task.title,
            description=task.description,
            status=task.status.value,
            priority=task.priority.value,
            assignee_id=task.assignee_id,
            due_date=task.due_date,
            position=task.position,
            labels=list(task.labels),
            created_by=task.created_by,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
        self._session.add(model)
        self._session.commit()
        return _to_entity(model)

    def find_by_id(self, task_id: UUID) -> Optional[Task]:
        m = self._session.query(TaskModel).filter_by(id=task_id).first()
        return _to_entity(m) if m else None

    def list_by_project(
        self,
        project_id: UUID,
        status: Optional[TaskStatus] = None,
    ) -> list[Task]:
        q = self._session.query(TaskModel).filter_by(project_id=project_id)
        if status is not None:
            q = q.filter_by(status=status.value)
        rows = q.order_by(asc(TaskModel.status), asc(TaskModel.position), asc(TaskModel.created_at)).all()
        return [_to_entity(m) for m in rows]

    def update(self, task: Task) -> Task:
        m = self._session.query(TaskModel).filter_by(id=task.id).first()
        if m is None:
            raise LookupError(f"Task {task.id} not found")
        m.title = task.title
        m.description = task.description
        m.status = task.status.value
        m.priority = task.priority.value
        m.assignee_id = task.assignee_id
        m.due_date = task.due_date
        m.position = task.position
        m.labels = list(task.labels)
        self._session.commit()
        return _to_entity(m)

    def delete(self, task_id: UUID) -> bool:
        result = self._session.query(TaskModel).filter_by(id=task_id).delete()
        self._session.commit()
        return result > 0

    def max_position(self, project_id: UUID, status: TaskStatus) -> int:
        result = (
            self._session.query(func.coalesce(func.max(TaskModel.position), 0))
            .filter_by(project_id=project_id, status=status.value)
            .scalar()
        )
        return int(result or 0)
