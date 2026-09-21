"""Phase 04 — labor handlers on channels (catalogue 2.1-2.3).

Day roster (2.1, read-only, NEVER money — mirrors ``GetDayRosterUseCase``'s own D3
whitelist), bulk attendance (2.2 — a name match against the project's own workers,
confirmed by a ``choice``, then ``BulkLogAttendanceUseCase`` with the asker's own id),
validate pending days (2.3 — one option per pending entry plus "all").

Every write goes through the existing use case unchanged; this module only adds the
chat-native "match names from free text -> confirm -> execute" flow around it. A
permission check (``project:manage_labor``) runs both when the confirm choice is first
offered and again right before the write, so a stale choice tapped after a role change
can never bypass authorization.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from app.application.assistant import reply
from app.application.assistant.messages import AssistantMessenger
from app.application.assistant.models import ChannelScope
from app.application.authz.ports import AuthzReaderPort
from app.application.labor.bulk_log_attendance import (
    BulkLogAttendanceEntry,
    BulkLogAttendanceRequest,
    BulkLogAttendanceUseCase,
    ConflictsNotAcknowledgedError,
)
from app.application.labor.get_day_roster_usecase import (
    STATUS_ABSENT,
    GetDayRosterRequest,
    GetDayRosterUseCase,
)
from app.application.labor.list_pending_attendance import ListPendingAttendanceUseCase
from app.application.labor.ports import IWorkerRepository
from app.application.labor.validate_attendance import (
    ValidateAttendanceRequest,
    ValidateAttendanceUseCase,
)
from app.application.projects.ports import IProjectRepository
from app.domain.authz.resolver import has_permission
from app.domain.entities.labor_entry import STATUS_PENDING
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError

MANAGE_LABOR_PERMISSION = "project:manage_labor"

#: How many pending-entry options `validate_attendance` offers before the "all" option —
#: keeps the choice card from growing unboundedly on a very backed-up project.
_MAX_PENDING_OPTIONS = 10


def _worker_permitted(authz_reader: AuthzReaderPort, user_id: UUID, project_id: UUID) -> bool:
    return has_permission(
        authz_reader,
        user_id,
        MANAGE_LABOR_PERMISSION,
        project_id=project_id,
        is_platform_admin=authz_reader.is_platform_ops(user_id),
    )


class LaborFeature:
    """Day roster, bulk attendance and pending-day validation from a chat channel."""

    def __init__(
        self,
        *,
        authz_reader: AuthzReaderPort,
        worker_repo: IWorkerRepository,
        project_repo: IProjectRepository,
        day_roster_usecase: GetDayRosterUseCase,
        bulk_log_usecase: BulkLogAttendanceUseCase,
        validate_usecase: ValidateAttendanceUseCase,
        pending_attendance_usecase: ListPendingAttendanceUseCase,
    ) -> None:
        self._authz_reader = authz_reader
        self._worker_repo = worker_repo
        self._project_repo = project_repo
        self._day_roster_usecase = day_roster_usecase
        self._bulk_log_usecase = bulk_log_usecase
        self._validate_usecase = validate_usecase
        self._pending_attendance_usecase = pending_attendance_usecase

    # ------------------------------------------------------------------
    # 2.1 — day roster (read-only, never money)
    # ------------------------------------------------------------------

    def ask_roster(
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
        request = GetDayRosterRequest(
            project_id=project_id,
            date=date.today(),
            caller_user_id=user_id,
            is_platform_admin=self._authz_reader.is_platform_ops(user_id),
        )
        rows = self._day_roster_usecase.execute(request)
        if rows is None:
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        if not rows:
            messenger.post_text(
                user_id,
                reply.render("roster_empty", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "replied"
        lines = []
        for row in rows:
            if row.status == STATUS_ABSENT:
                lines.append(reply.render("roster_line_absent", lang, name=row.name))
            elif row.status == STATUS_PENDING:
                lines.append(
                    reply.render(
                        "roster_line_pending", lang, name=row.name, hours=row.hours, day_type=row.day_type or "-"
                    )
                )
            else:
                lines.append(
                    reply.render(
                        "roster_line_present", lang, name=row.name, hours=row.hours, day_type=row.day_type or "-"
                    )
                )
        messenger.post_text(
            user_id, "\n".join(lines), reply_to_id=message_id, trace_id=trace_id, channel=scope.channel, scope=scope
        )
        return "replied"

    # ------------------------------------------------------------------
    # 2.2 — bulk attendance (name match -> confirm -> write)
    # ------------------------------------------------------------------

    def log_attendance(
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
        if not _worker_permitted(self._authz_reader, user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "refused"
        workers = self._worker_repo.list_by_project(project_id, active_only=True)
        needle = text.lower()
        matched = [w for w in workers if w.name.strip().lower() and w.name.strip().lower() in needle]
        if not matched:
            messenger.post_text(
                user_id,
                reply.render("attendance_need_names", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "asked"
        today = date.today()
        project = self._project_repo.find_by_id(project_id)
        project_name = project.name if project is not None else str(project_id)
        names = ", ".join(w.name for w in matched)
        prompt = reply.render(
            "attendance_confirm_prompt", lang, names=names, date=today.isoformat(), project=project_name
        )
        options = [
            {
                "label": reply.render("attendance_confirm_yes", lang),
                "action": "confirm_bulk_attendance",
                "payload": {
                    "project_id": str(project_id),
                    "worker_ids": [str(w.id) for w in matched],
                    "date": today.isoformat(),
                },
            },
            {
                "label": reply.render("attendance_confirm_no", lang),
                "action": "cancel_bulk_attendance",
                "payload": {},
            },
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

    def confirm_bulk_attendance(
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
        if not _worker_permitted(self._authz_reader, user_id, project_id):
            messenger.post_text(
                user_id,
                reply.render("no_permission", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        worker_ids = [UUID(str(w)) for w in payload.get("worker_ids", [])]
        day = date.fromisoformat(str(payload["date"]))
        request = BulkLogAttendanceRequest(
            project_id=project_id,
            date=day,
            entries=[BulkLogAttendanceEntry(worker_id=w) for w in worker_ids],
        )
        try:
            response = self._bulk_log_usecase.execute(request)
        except (WorkerNotFoundError, ConflictsNotAcknowledgedError):
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return
        messenger.post_text(
            user_id,
            reply.render(
                "attendance_logged", lang, created=len(response.created), skipped=len(response.skipped_worker_ids)
            ),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )

    # ------------------------------------------------------------------
    # 2.3 — validate pending self-logged days
    # ------------------------------------------------------------------

    def validate_attendance(
        self,
        *,
        scope: ChannelScope,
        user_id: UUID,
        message_id: UUID,
        lang: str,
        messenger: AssistantMessenger,
        trace_id: str,
    ) -> str:
        items = self._pending_attendance_usecase.execute(user_id=user_id)
        if scope.kind == "project":
            items = [item for item in items if item.project_id == str(scope.project_id)]
        if not items:
            messenger.post_text(
                user_id,
                reply.render("validate_attendance_none_pending", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "replied"
        shown = items[:_MAX_PENDING_OPTIONS]
        options = [
            {
                "label": f"{item.worker_name} — {item.date} — {item.project_name}",
                "action": "confirm_validate_attendance",
                "payload": {"entry_ids": [item.entry_id], "project_ids": [item.project_id]},
            }
            for item in shown
        ]
        options.append(
            {
                "label": reply.render("validate_attendance_all_option", lang),
                "action": "confirm_validate_attendance",
                "payload": {
                    "entry_ids": [item.entry_id for item in shown],
                    "project_ids": [item.project_id for item in shown],
                },
            }
        )
        messenger.post_choice(
            user_id,
            reply.render("validate_attendance_prompt", lang),
            options,
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            addressed_to=user_id,
            scope=scope,
        )
        return "asked"

    def confirm_validate_attendance(
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
        entry_ids = [UUID(str(e)) for e in payload.get("entry_ids", [])]
        project_ids = [UUID(str(p)) for p in payload.get("project_ids", [])]
        validated = 0
        for entry_id, project_id in zip(entry_ids, project_ids):
            if not _worker_permitted(self._authz_reader, user_id, project_id):
                continue
            try:
                self._validate_usecase.execute(
                    ValidateAttendanceRequest(entry_id=entry_id, project_id=project_id, validator_user_id=user_id)
                )
                validated += 1
            except Exception:  # pragma: no cover - defensive, matches the rest of the pipeline's try/except style
                continue
        messenger.post_text(
            user_id,
            reply.render("validate_attendance_done", lang, count=validated),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )


__all__ = ["LaborFeature", "MANAGE_LABOR_PERMISSION"]
