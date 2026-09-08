"""Get day roster use case (D3): name, presence, hours, day type — never money.

The member roster is the one place a plain `member` company role sees other
workers on a day, so authorization here is deliberately NOT the generic
project-read gate used everywhere else in `app.api.v1.projects.decorators`.
Only three shapes of caller may see it: a caller assigned to the project
(`user_projects` row), a company admin of the project's owning company, or a
platform `*:*` holder. The check is evaluated purely against
`AuthzReaderPort` (company role + assignment), never against the caller's raw
JWT `permissions` claim — a legacy global role that happens to carry the
literal string "project:view_roster" (or "project:read") must NOT grant
access on its own; only an actual company/project relationship does. See
`app.api.v1.labor.roster_routes` for why a failed check answers 404 (hiding
the project's existence) rather than the usual 403.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Optional
from uuid import UUID

from app.application.labor.ports import ILaborEntryRepository, IWorkerRepository
from app.domain.authz.resolver import has_permission
from app.domain.entities.labor_entry import STATUS_PENDING

if TYPE_CHECKING:
    from app.application.authz.ports import AuthzReaderPort

ROSTER_PERMISSION = "project:view_roster"

STATUS_PRESENT = "present"
STATUS_ABSENT = "absent"

# Hours worked for a given shift_type, before adding supplement_hours.
# shift_type is a PAY multiplier (see app.domain.labor.shift_multipliers —
# full=1.0x, half=0.5x, overtime=1.5x of the daily_rate), not a worked-hours
# multiplier: an "overtime" day is a full 8h day paid at a premium, not a
# 12-hour day. supplement_hours (see get_labor_summary.py's banked_hours) is
# the one field that always represents literal extra hours and is added on
# top of every shift_type, including a supplement-only row (shift_type=None).
_STANDARD_WORKDAY_HOURS = 8
_SHIFT_BASE_HOURS: dict[str, int] = {
    "full": _STANDARD_WORKDAY_HOURS,
    "overtime": _STANDARD_WORKDAY_HOURS,
    "half": _STANDARD_WORKDAY_HOURS // 2,
}


@dataclass(frozen=True)
class RosterRow:
    """One worker's day — the D3 whitelist. Never rate/cost/amount/total."""

    worker_id: UUID
    name: str
    status: str  # "present" | "pending" | "absent"
    hours: float
    day_type: Optional[str]  # the entry's shift_type, or None


@dataclass(frozen=True)
class GetDayRosterRequest:
    project_id: UUID
    date: date
    caller_user_id: UUID
    is_platform_admin: bool = False


class GetDayRosterUseCase:
    """Returns None when the caller may not see this project's roster at all."""

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
        authz_reader: "AuthzReaderPort",
    ) -> None:
        self._workers = worker_repo
        self._entries = entry_repo
        self._authz = authz_reader

    def execute(self, request: GetDayRosterRequest) -> Optional[list[RosterRow]]:
        if not self._authorized(request):
            return None

        workers = self._workers.list_by_project(request.project_id, active_only=True)
        entries = self._entries.list_by_project(request.project_id, date_from=request.date, date_to=request.date)
        entry_by_worker_id = {entry.worker_id: entry for entry in entries}

        rows: list[RosterRow] = []
        for worker in workers:
            entry = entry_by_worker_id.get(worker.id)
            if entry is None:
                rows.append(
                    RosterRow(worker_id=worker.id, name=worker.name, status=STATUS_ABSENT, hours=0.0, day_type=None)
                )
                continue
            status = STATUS_PENDING if entry.status == STATUS_PENDING else STATUS_PRESENT
            base_hours = _SHIFT_BASE_HOURS.get(entry.shift_type, 0) if entry.shift_type else 0
            rows.append(
                RosterRow(
                    worker_id=worker.id,
                    name=worker.name,
                    status=status,
                    hours=float(base_hours + entry.supplement_hours),
                    day_type=entry.shift_type,
                )
            )
        return rows

    def _authorized(self, request: GetDayRosterRequest) -> bool:
        """True iff the effective, DB-truth-only permission set grants the roster.

        Deliberately calls the pure resolver (`has_permission`) with the
        injected `AuthzReaderPort` directly — never the request's JWT
        `permissions` claim — so a stale/legacy global-role claim cannot
        substitute for an actual company role + assignment.
        """
        return has_permission(
            self._authz,
            request.caller_user_id,
            ROSTER_PERMISSION,
            project_id=request.project_id,
            is_platform_admin=request.is_platform_admin,
        )
