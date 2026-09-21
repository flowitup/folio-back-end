"""Phase 04 — tasks handlers on channels (catalogue 3.1-3.2).

Create task (3.1): title/due date parsed by DeepSeek JSON out of the chat utterance
(byte-identical French system prompt, plan hard rule #4), confirmed by a ``choice``,
then created via ``CreateTaskUseCase`` with the asker as ``created_by``. Open tasks this
week (3.2): a read-only listing, no model call — just ``ListTasksUseCase`` filtered to
the next 7 days.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.exceptions import LlmOutputError
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope, TaskDraft
from app.application.assistant.ports import VisionLlmPort
from app.application.projects.ports import IProjectRepository
from app.application.task.use_cases import CreateTaskRequest, CreateTaskUseCase, ListTasksUseCase
from app.domain.entities.task import TaskStatus

# Keep byte-identical across every call — same rule as extract.py's S1 prompt.
CREATE_TASK_SYSTEM_FR = (
    "Tu extrais le titre et la date d'échéance d'une tâche à créer sur un chantier BTP français, à partir d'un "
    "message de chat. Réponds uniquement avec un JSON aux champs : title (résumé court de la tâche, sans le mot "
    '"tâche"), due_date (YYYY-MM-DD, résous les expressions relatives comme "demain" ou "vendredi" par rapport à '
    "la date d'aujourd'hui fournie dans le message). Champs absents ou non mentionnés → null. N'invente rien."
)

#: How many days ahead "open tasks this week" looks (today inclusive).
_WEEK_WINDOW_DAYS = 7


class TasksFeature:
    """Create a task from free text (3.1) and list this week's open tasks (3.2)."""

    def __init__(
        self,
        *,
        vision: VisionLlmPort,
        project_repo: IProjectRepository,
        create_usecase: CreateTaskUseCase,
        list_usecase: ListTasksUseCase,
    ) -> None:
        self._vision = vision
        self._project_repo = project_repo
        self._create_usecase = create_usecase
        self._list_usecase = list_usecase

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
        today = date.today()
        user_text = f"Aujourd'hui : {today.isoformat()}. Message : {text}"
        try:
            draft = self._vision.chat_json(
                system=CREATE_TASK_SYSTEM_FR, user_text=user_text, images=[], model_cls=TaskDraft
            )
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
        title = (draft.title or "").strip()
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
                "task_confirm_prompt_with_due", lang, title=title, due=due_date.isoformat(), project=project_name
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
    ) -> None:
        project_id = UUID(str(payload["project_id"]))
        title = str(payload["title"])
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
        today = date.today()
        window_end = today + timedelta(days=_WEEK_WINDOW_DAYS)
        tasks = [
            task
            for task in self._list_usecase.execute(project_id)
            if task.status != TaskStatus.DONE and (task.due_date is None or today <= task.due_date <= window_end)
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
        lines = [
            (
                reply.render("tasks_line", lang, title=task.title, due=task.due_date.isoformat())
                if task.due_date
                else reply.render("tasks_line_no_due", lang, title=task.title)
            )
            for task in tasks
        ]
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
