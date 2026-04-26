"""Task repository port — persistence contract for the planning domain."""

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from app.domain.entities.task import Task, TaskStatus


class ITaskRepository(ABC):
    """Persistence contract for Kanban tasks."""

    @abstractmethod
    def create(self, task: Task) -> Task: ...

    @abstractmethod
    def find_by_id(self, task_id: UUID) -> Optional[Task]: ...

    @abstractmethod
    def list_by_project(
        self,
        project_id: UUID,
        status: Optional[TaskStatus] = None,
    ) -> list[Task]:
        """Return all tasks for a project, ordered by status then position ASC."""

    @abstractmethod
    def update(self, task: Task) -> Task: ...

    @abstractmethod
    def delete(self, task_id: UUID) -> bool: ...

    @abstractmethod
    def max_position(self, project_id: UUID, status: TaskStatus) -> int:
        """Return the highest position in the given lane (0 if empty)."""
