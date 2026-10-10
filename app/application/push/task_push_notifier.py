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
from app.application.push.project_label import project_label
from app.application.task.use_cases import InvalidAssigneeError, _assert_assignee_can_read
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
    "vi": {
        "backlog": "Việc tồn đọng",
        "todo": "Cần làm",
        "in_progress": "Đang làm",
        "blocked": "Bị chặn",
        "done": "Xong",
    },
    "fr": {"backlog": "Backlog", "todo": "À faire", "in_progress": "En cours", "blocked": "Bloqué", "done": "Terminé"},
    "en": {"backlog": "Backlog", "todo": "To do", "in_progress": "In progress", "blocked": "Blocked", "done": "Done"},
}


class ProjectNameReader(Protocol):
    def find_by_id(self, project_id: UUID): ...


class TaskPushNotifier:
    def __init__(
        self,
        dispatcher: PushDispatcher,
        project_repo: ProjectNameReader,
        authz_reader=None,
        user_repo=None,
    ) -> None:
        self._dispatcher = dispatcher
        self._projects = project_repo
        self._authz_reader = authz_reader
        self._user_repo = user_repo

    def set_access_reader(self, authz_reader, user_repo=None) -> None:
        """Wired after construction: the authz reader is built later than the push stack."""
        self._authz_reader = authz_reader
        self._user_repo = user_repo

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
            # An assignee removed from the project (or the company, or deactivated)
            # keeps the stale task row; they must not hear about it any more.
            try:
                _assert_assignee_can_read(self._authz_reader, assignee, task.project_id, self._user_repo)
            except InvalidAssigneeError:
                return
            project = self._projects.find_by_id(task.project_id)
            status_value = getattr(getattr(task, "status", None), "value", "") or ""

            def render(locale: str) -> tuple[str, str]:
                title, body = _TEXT[event][locale]
                status = _STATUS_LABELS.get(locale, {}).get(status_value, status_value)
                return title, body.format(title=task.title, project=project_label(project), status=status)

            self._dispatcher.dispatch(
                category=NotificationCategory.TASKS.value,
                recipients=[assignee],
                render=render,
                data={
                    "kind": f"task_{event}",
                    "project_id": str(task.project_id),
                    "task_id": str(task.id),
                },
                exclude=actor_id,
            )
        except Exception:  # a push must never break the task operation
            logger.exception("task push failed event=%s", event)
