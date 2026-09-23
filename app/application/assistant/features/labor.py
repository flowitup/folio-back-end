"""Phase 04 — labor handlers on channels (catalogue 2.1-2.3).

Day roster (2.1, read-only, NEVER money — mirrors ``GetDayRosterUseCase``'s own D3
whitelist), bulk attendance (2.2 — a name match against the project's own workers,
confirmed by a ``choice``, then ``BulkLogAttendanceUseCase`` with the asker's own id),
validate pending days (2.3 — one option per pending entry plus "all").

Every write goes through the existing use case unchanged; this module only adds the
chat-native "match names from free text -> confirm -> execute" flow around it. For
``log_attendance``/``confirm_bulk_attendance``, a permission check
(``project:manage_labor``) runs both when the confirm choice is first offered and again
right before the write, so a stale choice tapped after a role change can never bypass
authorization. ``validate_attendance`` spans however many projects a company/admin
channel's pending list touches, so there is no single project to gate the LISTING on —
``ListPendingAttendanceUseCase`` already scopes each item to what the caller may
validate, and a company/admin scope is additionally narrowed to that channel's own
company; ``confirm_validate_attendance`` re-checks per entry, right before each
write, exactly like the bulk-attendance write does.

Every "today" below is the site's own calendar date (``app.domain.time.business_today``,
Europe/Paris), not the server's UTC clock — the day changes for a caller in Paris, not
when the server's clock rolls over.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional, Protocol
from uuid import UUID

from app.application.assistant import formatting, reply
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
from app.domain.exceptions.labor_exceptions import InvalidLaborEntryError, WorkerNotFoundError
from app.domain.time import business_today

logger = logging.getLogger(__name__)

MANAGE_LABOR_PERMISSION = "project:manage_labor"

#: How many pending-entry options `validate_attendance` offers before the "all" option —
#: keeps the choice card from growing unboundedly on a very backed-up project.
_MAX_PENDING_OPTIONS = 10

#: The single day shape a chat-driven "X, Y went to work today" confirm logs. Free text
#: never carries enough signal to tell "half day" or "overtime" apart from "full day", so
#: bulk attendance from chat only ever logs a full day — a worker whose day is actually a
#: half day or has overtime still gets it corrected the normal way (the app, or a change
#: request).
_CHAT_LOGGED_SHIFT_TYPE = "full"


class AttendanceNotifierPort(Protocol):
    """The one method `confirm_validate_attendance` needs from the same push notifier
    the HTTP validate route calls — kept as a narrow structural port here instead of
    importing the concrete notifier, so this module does not reach into `app.application.push`."""

    def decision(self, worker_id: UUID, day: date, entry_id: UUID, event: str) -> None: ...


#: New copy needed by the labor handlers that has no existing `reply.py` template —
#: kept local (same fixed-template-per-language shape `reply.render` uses) rather than
#: growing the shared template registry for this module's own additions.
_LOCAL_TEMPLATES: dict[str, dict[str, str]] = {
    "attendance_invalid_entry": {
        "vi": "Không thể ghi công cho {names}: dữ liệu không hợp lệ.",
        "fr": "Impossible d'enregistrer la présence de {names} : donnée invalide.",
        "en": "Could not log attendance for {names}: invalid entry.",
    },
    "attendance_conflict_prompt": {
        "vi": "{names} đã có công ngày {date} ở công trình khác: {projects}. Vẫn ghi nhận?",
        "fr": "{names} a/ont déjà du pointage le {date} sur un autre chantier : {projects}. Enregistrer quand même ?",
        "en": "{names} already has attendance logged on {date} on another project: {projects}. Log anyway?",
    },
    "attendance_conflict_confirm": {
        "vi": "Vẫn ghi nhận",
        "fr": "Enregistrer quand même",
        "en": "Log anyway",
    },
}


def _local_render(key: str, lang: str, **kwargs: object) -> str:
    resolved_lang = lang if lang in reply.LANGUAGES else "fr"
    return _LOCAL_TEMPLATES[key][resolved_lang].format(**kwargs)


def _worker_display_name(worker_repo: IWorkerRepository, worker_id: UUID) -> str:
    """The worker's name for a user-facing reply — never the raw UUID. Falls back to
    the id (still readable, never crashes) when the worker cannot be resolved."""
    worker = worker_repo.find_by_id(worker_id)
    return worker.name if worker is not None else str(worker_id)


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
        notifier: Optional[AttendanceNotifierPort] = None,
    ) -> None:
        self._authz_reader = authz_reader
        self._worker_repo = worker_repo
        self._project_repo = project_repo
        self._day_roster_usecase = day_roster_usecase
        self._bulk_log_usecase = bulk_log_usecase
        self._validate_usecase = validate_usecase
        self._pending_attendance_usecase = pending_attendance_usecase
        self._notifier = notifier

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
            date=business_today(),
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
                        "roster_line_pending",
                        lang,
                        name=row.name,
                        hours=row.hours,
                        day_type=formatting.shift_type_label(row.day_type, lang),
                    )
                )
            else:
                lines.append(
                    reply.render(
                        "roster_line_present",
                        lang,
                        name=row.name,
                        hours=row.hours,
                        day_type=formatting.shift_type_label(row.day_type, lang),
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
        name_by_worker_id = {w.id: w.name.strip() for w in workers if w.name and w.name.strip()}
        matched_names = formatting.match_names(text, name_by_worker_id.values())
        matched = [w for w in workers if name_by_worker_id.get(w.id) in matched_names]
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
        today = business_today()
        project = self._project_repo.find_by_id(project_id)
        project_name = project.name if project is not None else str(project_id)
        names = ", ".join(w.name for w in matched)
        prompt = reply.render(
            "attendance_confirm_prompt",
            lang,
            names=names,
            date=formatting.format_date(today, lang),
            project=project_name,
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
    ) -> str:
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
            return "refused"
        worker_ids = [UUID(str(w)) for w in payload.get("worker_ids", [])]
        day = date.fromisoformat(str(payload["date"]))
        request = BulkLogAttendanceRequest(
            project_id=project_id,
            date=day,
            entries=[BulkLogAttendanceEntry(worker_id=w, shift_type=_CHAT_LOGGED_SHIFT_TYPE) for w in worker_ids],
            acknowledge_conflicts=bool(payload.get("acknowledge_conflicts", False)),
        )
        try:
            response = self._bulk_log_usecase.execute(request)
        except WorkerNotFoundError:
            messenger.post_text(
                user_id,
                reply.render("error", lang),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "error"
        except InvalidLaborEntryError:
            # Names, not raw UUIDs — a worker who cannot be resolved anymore (deleted
            # between the choice being offered and this submit) falls back to their id
            # rather than breaking the reply.
            names = ", ".join(_worker_display_name(self._worker_repo, w) for w in worker_ids)
            messenger.post_text(
                user_id,
                _local_render("attendance_invalid_entry", lang, names=names),
                reply_to_id=message_id,
                trace_id=trace_id,
                channel=scope.channel,
                scope=scope,
            )
            return "error"
        except ConflictsNotAcknowledgedError as exc:
            names = ", ".join(sorted({c.person_name for c in exc.conflicts}))
            projects = ", ".join(sorted({e.project_name for c in exc.conflicts for e in c.entries}))
            prompt = _local_render(
                "attendance_conflict_prompt",
                lang,
                names=names or "?",
                date=formatting.format_date(day, lang),
                projects=projects or "?",
            )
            options = [
                {
                    "label": _local_render("attendance_conflict_confirm", lang),
                    "action": "confirm_bulk_attendance",
                    "payload": {**payload, "acknowledge_conflicts": True},
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
        return "answered"

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
        # Scoped in the query itself (not fetched-then-filtered): a project channel asks
        # for that project's own 100, a company/admin channel for that company's own
        # 100 — never the first 100 across every company/project the caller may validate,
        # with today's own scope applied only after the cap already dropped some of it.
        items = self._pending_attendance_usecase.execute(
            user_id=user_id,
            company_id=scope.company_id if scope.kind != "project" else None,
            project_id=scope.project_id if scope.kind == "project" else None,
        )
        # A worker's open change request on an already-validated day (`kind ==
        # "attendance_change"`) is not a pending entry: `ValidateAttendanceUseCase` is a
        # no-op on it (it only flips `pending` -> `validated`), so offering it here would
        # let the caller tap "validate" and be told it worked while the change request
        # stays open and the day unchanged. Only real pending entries are offered.
        items = [item for item in items if item.kind == "attendance_pending"]
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
                "label": f"{item.worker_name} — {formatting.format_date(date.fromisoformat(item.date), lang)} — "
                f"{item.project_name}",
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
    ) -> str:
        entry_ids = [UUID(str(e)) for e in payload.get("entry_ids", [])]
        project_ids = [UUID(str(p)) for p in payload.get("project_ids", [])]
        validated = 0
        any_permission_denied = False
        for entry_id, project_id in zip(entry_ids, project_ids):
            if not _worker_permitted(self._authz_reader, user_id, project_id):
                any_permission_denied = True
                continue
            try:
                response = self._validate_usecase.execute(
                    ValidateAttendanceRequest(entry_id=entry_id, project_id=project_id, validator_user_id=user_id)
                )
                validated += 1
            except Exception:  # pragma: no cover - defensive, matches the rest of the pipeline's try/except style
                continue
            self._notify_worker_validated(response)
        messenger.post_text(
            user_id,
            reply.render("validate_attendance_done", lang, count=validated),
            reply_to_id=message_id,
            trace_id=trace_id,
            channel=scope.channel,
            scope=scope,
        )
        # A fully stale tap — every entry re-checked and denied by `_worker_permitted`
        # since the choice was offered — must be audited as refused, not as a successful
        # "0 journée(s) validée(s)" answer.
        if validated == 0 and entry_ids and any_permission_denied:
            return "refused"
        return "answered"

    def _notify_worker_validated(self, response: Any) -> None:
        """Tell the worker their day was validated — the same push the HTTP validate
        route sends; without this, a day validated through @folio never reaches the
        worker who logged it."""
        if self._notifier is None:
            return
        try:
            self._notifier.decision(
                UUID(response.worker_id), date.fromisoformat(response.date), UUID(response.id), "validated"
            )
        except Exception:  # pragma: no cover - a push failure must never break the reply
            logger.exception("attendance validation push failed")


__all__ = ["LaborFeature", "MANAGE_LABOR_PERMISSION", "AttendanceNotifierPort"]
