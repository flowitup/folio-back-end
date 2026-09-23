"""Phase 04 — tasks handlers on channels (catalogue 3.1-3.2).

Create task (3.1): title/due date parsed by DeepSeek JSON out of the chat utterance
(byte-identical French system prompt, plan hard rule #4), confirmed by a ``choice``,
then created via ``CreateTaskUseCase`` with the asker as ``created_by``. Open tasks this
week (3.2): a read-only listing, no model call — ``ListTasksUseCase`` filtered to
overdue + the next 7 days, capped so the reply never grows past the chat body limit.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app.application.assistant import formatting, reply
from app.application.assistant.exceptions import LlmOutputError, LlmUnavailableError
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, TaskDraft
from app.application.assistant.ports import VisionLlmPort
from app.application.authz.ports import AuthzReaderPort
from app.application.projects.ports import IProjectRepository
from app.application.task.use_cases import CreateTaskRequest, CreateTaskUseCase, ListTasksUseCase
from app.domain.authz.resolver import has_permission
from app.domain.entities.task import TaskStatus
from app.domain.time import business_today

#: The task routes (`GET`/`POST .../projects/<id>/tasks`) both gate on this single
#: permission (`app/api/v1/tasks/task_routes.py`) — any project member may list or
#: create tasks, matching the resolver's own `project:read` grant. Every handler here
#: re-checks it explicitly: `resolve_project`'s candidate list is
#: already filtered to readable projects, but a stale `pick_project_ctx`/
#: `confirm_create_task` tap replays against whatever project_id it was given, so the
#: check must happen again right before acting, not just when the choice was offered.
TASK_PERMISSION = "project:read"

# Keep byte-identical across every call — same rule as extract.py's S1 prompt.
CREATE_TASK_SYSTEM_FR = (
    "Tu extrais le titre et la date d'échéance d'une tâche à créer sur un chantier BTP français, à partir d'un "
    "message de chat. Réponds uniquement avec un JSON aux champs : title (résumé court de la tâche, sans le mot "
    '"tâche"), due_date (YYYY-MM-DD, résous les expressions relatives comme "demain" ou "vendredi" par rapport à '
    "la date d'aujourd'hui fournie dans le message). Champs absents ou non mentionnés → null. N'invente rien."
)

#: How many days ahead "open tasks this week" looks (today inclusive).
_WEEK_WINDOW_DAYS = 7

#: The column backing a task title is `String(255)` (`app/infrastructure/database/models/task.py`)
#: — a longer title from a free-text parse must be cut before it ever reaches the insert,
#: or the write fails after the user has already tapped "confirm".
_TASK_TITLE_MAX_LENGTH = 255

#: `ask_tasks` caps its listing so the reply never approaches the chat body limit on a
#: project with a large backlog; the rest is summarised as "+N".
_MAX_TASK_LINES = 15

#: New copy this module needs that has no existing `reply.py` template.
_LOCAL_TEMPLATES: dict[str, dict[str, str]] = {
    "tasks_more": {
        "vi": "…và {count} việc khác.",
        "fr": "… et {count} autre(s) tâche(s).",
        "en": "…and {count} more task(s).",
    },
}


def _local_render(key: str, lang: str, **kwargs: object) -> str:
    resolved_lang = lang if lang in reply.LANGUAGES else "fr"
    return _LOCAL_TEMPLATES[key][resolved_lang].format(**kwargs)


class TasksFeature:
    """Create a task from free text (3.1) and list this week's open tasks (3.2)."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        project_repo: IProjectRepository,
        create_usecase: CreateTaskUseCase,
        list_usecase: ListTasksUseCase,
        authz_reader: AuthzReaderPort,
    ) -> None:
        self._vision = vision
        self._project_repo = project_repo
        self._create_usecase = create_usecase
        self._list_usecase = list_usecase
        self._authz_reader = authz_reader

    def _permitted(self, user_id: UUID, project_id: UUID) -> bool:
        return has_permission(
            self._authz_reader,
            user_id,
            TASK_PERMISSION,
            project_id=project_id,
            is_platform_admin=self._authz_reader.is_platform_ops(user_id),
        )

    # ------------------------------------------------------------------
    # 3.1 — create a task
    # ------------------------------------------------------------------

    def create_task(
        self,
        *,
        scope: ChannelScope,
        project_id: UUID,
        text: str,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        if not self._permitted(user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        today = business_today()
        user_text = f"Aujourd'hui : {today.isoformat()}. Message : {text}"
        try:
            draft = self._vision.chat_json(
                system=CREATE_TASK_SYSTEM_FR, user_text=user_text, images=[], model_cls=TaskDraft
            )
        except LlmUnavailableError:
            messenger.post_text(
                user_id,
                reply.render("provider_unavailable", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "error"
        except LlmOutputError:
            messenger.post_text(
                user_id,
                reply.render("task_need_title", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        title = (draft.title or "").strip()[:_TASK_TITLE_MAX_LENGTH]
        if not title:
            messenger.post_text(
                user_id,
                reply.render("task_need_title", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        due_date = _parse_due_date(draft.due_date)

        project = self._project_repo.find_by_id(project_id)
        project_name = project.name if project is not None else str(project_id)
        if due_date:
            prompt = reply.render(
                "task_confirm_prompt_with_due",
                lang,
                title=title,
                due=formatting.format_date(due_date, lang),
                project=project_name,
            )
        else:
            prompt = reply.render("task_confirm_prompt_no_due", lang, title=title, project=project_name)
        options = [
            {
                "label": reply.render("task_confirm_yes", lang),
                "action": "confirm_create_task",
                "payload": {
                    "project_id": str(project_id),
                    "title": title,
                    "due_date": due_date.isoformat() if due_date else None,
                },
            },
            {"label": reply.render("task_confirm_no", lang), "action": "cancel_create_task", "payload": {}},
        ]
        messenger.post_choice(
            user_id,
            prompt,
            options,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            addressed_to=user_id,
            scope=scope,
        )
        return "asked"

    def confirm_create_task(
        self,
        *,
        scope: ChannelScope,
        payload: dict[str, Any],
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        project_id = UUID(str(payload["project_id"]))
        # Re-checked right before the write (not just when the confirm choice was
        # offered): a role change between the offer and the tap must not let a stale
        # choice through (same pattern as `LaborFeature.confirm_bulk_attendance`).
        if not self._permitted(user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        title = str(payload["title"])[:_TASK_TITLE_MAX_LENGTH]
        due_date = _parse_due_date(payload.get("due_date"))
        task = self._create_usecase.execute(
            CreateTaskRequest(project_id=project_id, title=title, due_date=due_date, created_by=user_id)
        )
        messenger.post_text(
            user_id,
            reply.render("task_created", lang, title=task.title),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        return "answered"

    # ------------------------------------------------------------------
    # 3.2 — open tasks this week (read-only)
    # ------------------------------------------------------------------

    def ask_tasks(
        self,
        *,
        scope: ChannelScope,
        project_id: UUID,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        if not self._permitted(user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        today = business_today()
        window_end = today + timedelta(days=_WEEK_WINDOW_DAYS)
        # Overdue tasks (due before today) are the most important ones to surface, not
        # left out: anything with no due date, or due on/before the end of the window,
        # is in scope — only a due date further out than the window is excluded.
        tasks = [
            task
            for task in self._list_usecase.execute(project_id)
            if task.status != TaskStatus.DONE and (task.due_date is None or task.due_date <= window_end)
        ]
        if not tasks:
            messenger.post_text(
                user_id,
                reply.render("tasks_none_open", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "replied"
        tasks.sort(key=lambda t: (t.due_date is None, t.due_date or date.max))
        shown = tasks[:_MAX_TASK_LINES]
        lines = [
            (
                reply.render("tasks_line", lang, title=task.title, due=formatting.format_date(task.due_date, lang))
                if task.due_date
                else reply.render("tasks_line_no_due", lang, title=task.title)
            )
            for task in shown
        ]
        if len(tasks) > _MAX_TASK_LINES:
            lines.append(_local_render("tasks_more", lang, count=len(tasks) - _MAX_TASK_LINES))
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "replied"


def _parse_due_date(value: "str | None") -> "date | None":
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["TasksFeature", "CREATE_TASK_SYSTEM_FR"]
