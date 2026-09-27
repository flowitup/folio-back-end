"""Push notifications about tasks — the assignee is the only stakeholder.

Work handed to somebody, or moved under them, is worth interrupting for; everything else
about a task (title edits, labels, priority) is not, so only assignment and column moves
notify.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Protocol
from uuid import UUID

from app.application.push.dispatcher import PushDispatcher
from app.domain.notifications.categories import NotificationCategory

logger = logging.getLogger(__name__)

_TEXT: Dict[str, Dict[str, tuple]] = {
    "assigned": {
        "vi": ("Công việc mới", "{title} · {project}"),
        "fr": ("Nouvelle tâche", "{title} · {project}"),
        "en": ("New task", "{title} · {project}"),
    },
    "moved": {
        "vi": ("Công việc đã chuyển cột", "{title} → {status} · {project}"),
        "fr": ("Tâche déplacée", "{title} → {status} · {project}"),
        "en": ("Task moved", "{title} → {status} · {project}"),
    },
}

# Column names as the board shows them (web messages `tasks.column`, plus backlog).
_STATUS_LABELS: Dict[str, Dict[str, str]] = {
    "vi": {"backlog": "Backlog", "todo": "Cần làm", "in_progress": "Đang làm", "blocked": "Bị chặn", "done": "Xong"},
    "fr": {"backlog": "Backlog", "todo": "À faire", "in_progress": "En cours", "blocked": "Bloqué", "done": "Terminé"},
    "en": {"backlog": "Backlog", "todo": "To do", "in_progress": "In progress", "blocked": "Blocked", "done": "Done"},
}


class ProjectNameReader(Protocol):
    def find_by_id(self, project_id: UUID): ...


class TaskPushNotifier:
    def __init__(self, dispatcher: PushDispatcher, project_repo: ProjectNameReader) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo

    def task_assigned(self, *, task, actor_id: UUID) -> None:
        """A task gained an assignee (on create, or a changed assignee on update)."""
        self._notify("assigned", task=task, actor_id=actor_id)

    def task_moved(self, *, task, actor_id: UUID) -> None:
        """A task changed status column; its assignee should know."""
        self._notify("moved", task=task, actor_id=actor_id)

    def _notify(self, event: str, *, task, actor_id: UUID) -> None:
        try:
            assignee: Optional[UUID] = getattr(task, "assignee_id", None)
            if assignee is None or assignee == actor_id:
                return
            project = self._projects.find_by_id(task.project_id)
            locale = self._dispatcher.locale
            title, body = _TEXT[event][locale]
            status = getattr(getattr(task, "status", None), "value", "") or ""
            status = _STATUS_LABELS.get(locale, {}).get(status, status)
            self._dispatcher.dispatch(
                category=NotificationCategory.TASKS.value,
                recipients=[assignee],
                title=title,
                body=body.format(
                    title=task.title,
                    project=project.name if project is not None else "",
                    status=status,
                ),
                data={
                    "kind": f"task_{event}",
                    "project_id": str(task.project_id),
                    "task_id": str(task.id),
                },
                exclude=actor_id,
            )
        except Exception:  # a push must never break the task operation
            logger.exception("task push failed event=%s", event)
