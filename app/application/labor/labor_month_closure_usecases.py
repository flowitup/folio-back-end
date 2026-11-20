"""Use cases for closing a labor month ("Chốt tháng", owner decision QĐ-10).

A manager marks a project's month as closed once its wages are settled. It is
deliberately not a lock: every write into a closed month still succeeds, and
:class:`RecordClosedMonthEditsUseCase` records who changed what afterwards, so the
month's "edited after close" warning can name them.

Reopening is an undo: the closure and the edits recorded against it are removed.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Optional
from uuid import UUID, uuid4

from app.application.labor.ports import ILaborEntryRepository, ILaborMonthClosureRepository
from app.domain.entities.labor_month_closure import LaborMonthClosure, LaborMonthClosureEdit
from app.domain.time import business_today


class MonthInFutureError(Exception):
    """A month that has not started yet cannot be closed."""


class MonthNotClosedError(Exception):
    """Reopening a month that is not closed."""


def month_start(day: date) -> date:
    return day.replace(day=1)


def month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@dataclass
class ClosureEditDetail:
    kind: str
    edited_at: Optional[str]
    user_id: Optional[str]
    user_name: Optional[str]
    entry_id: Optional[str]
    worker_id: Optional[str]
    worker_name: Optional[str]
    entry_date: Optional[str]


@dataclass
class LaborMonthClosureDetail:
    project_id: str
    month: str  # YYYY-MM
    closed_at: Optional[str]
    closed_by_user_id: Optional[str]
    closed_by_name: Optional[str]
    edited_after_close: int
    last_edited_at: Optional[str]
    last_edited_by_user_id: Optional[str]
    last_edited_by_name: Optional[str]
    edits: List[ClosureEditDetail] = field(default_factory=list)  # newest first


@dataclass
class CloseLaborMonthRequest:
    project_id: UUID
    month: date  # any day of the month
    user_id: Optional[UUID] = None


@dataclass
class ReopenLaborMonthRequest:
    project_id: UUID
    month: date


@dataclass
class ListLaborMonthClosuresRequest:
    project_id: UUID
    month: Optional[date] = None


@dataclass
class ClosedMonthTouch:
    """One write that may land in a closed month: ``day`` picks the month."""

    day: date
    kind: str
    entry_id: Optional[UUID] = None
    worker_id: Optional[UUID] = None
    entry_date: Optional[date] = None


def _details(repo: ILaborMonthClosureRepository, closures: List[LaborMonthClosure]) -> List[LaborMonthClosureDetail]:
    if not closures:
        return []
    edits_by_closure = repo.list_edits([c.id for c in closures])
    user_ids = {c.closed_by for c in closures if c.closed_by}
    worker_ids = set()
    for edits in edits_by_closure.values():
        user_ids |= {e.user_id for e in edits if e.user_id}
        worker_ids |= {e.worker_id for e in edits if e.worker_id}
    names = repo.user_names([u for u in user_ids if u is not None])
    workers = repo.worker_names([w for w in worker_ids if w is not None])

    out: List[LaborMonthClosureDetail] = []
    for c in closures:
        edits = sorted(edits_by_closure.get(c.id, []), key=lambda e: e.edited_at, reverse=True)
        rows = [
            ClosureEditDetail(
                kind=e.kind,
                edited_at=_iso(e.edited_at),
                user_id=str(e.user_id) if e.user_id else None,
                user_name=names.get(e.user_id) if e.user_id else None,
                entry_id=str(e.entry_id) if e.entry_id else None,
                worker_id=str(e.worker_id) if e.worker_id else None,
                worker_name=workers.get(e.worker_id) if e.worker_id else None,
                entry_date=e.entry_date.isoformat() if e.entry_date else None,
            )
            for e in edits
        ]
        last = rows[0] if rows else None
        out.append(
            LaborMonthClosureDetail(
                project_id=str(c.project_id),
                month=c.month.strftime("%Y-%m"),
                closed_at=_iso(c.closed_at),
                closed_by_user_id=str(c.closed_by) if c.closed_by else None,
                closed_by_name=names.get(c.closed_by) if c.closed_by else None,
                edited_after_close=len(rows),
                last_edited_at=last.edited_at if last else None,
                last_edited_by_user_id=last.user_id if last else None,
                last_edited_by_name=last.user_name if last else None,
                edits=rows,
            )
        )
    return out


class CloseLaborMonthUseCase:
    """Close (project, month). Idempotent: closing a closed month returns it unchanged.

    Returns ``(detail, created)``.
    """

    def __init__(self, repo: ILaborMonthClosureRepository) -> None:
        self._repo = repo

    def execute(self, req: CloseLaborMonthRequest) -> tuple[LaborMonthClosureDetail, bool]:
        month = month_start(req.month)
        if month > month_start(business_today()):
            raise MonthInFutureError(month.strftime("%Y-%m"))
        existing = self._repo.find(req.project_id, month)
        if existing is not None:
            return _details(self._repo, [existing])[0], False
        saved = self._repo.create(
            LaborMonthClosure(
                id=uuid4(),
                project_id=req.project_id,
                month=month,
                closed_at=datetime.now(timezone.utc),
                closed_by=req.user_id,
            )
        )
        return _details(self._repo, [saved])[0], True


class ReopenLaborMonthUseCase:
    """Undo a closure; the edits recorded against it go with it."""

    def __init__(self, repo: ILaborMonthClosureRepository) -> None:
        self._repo = repo

    def execute(self, req: ReopenLaborMonthRequest) -> None:
        if not self._repo.delete(req.project_id, month_start(req.month)):
            raise MonthNotClosedError(req.month.strftime("%Y-%m"))


class ListLaborMonthClosuresUseCase:
    """The project's closed months (newest first), each with its edits after closing."""

    def __init__(self, repo: ILaborMonthClosureRepository) -> None:
        self._repo = repo

    def execute(self, req: ListLaborMonthClosuresRequest) -> List[LaborMonthClosureDetail]:
        months = [month_start(req.month)] if req.month else None
        closures = sorted(self._repo.list_by_project(req.project_id, months), key=lambda c: c.month, reverse=True)
        return _details(self._repo, closures)


class RecordClosedMonthEditsUseCase:
    """Record writes that landed in closed months. Never blocks anything.

    Returns the closed months touched ("YYYY-MM", sorted) so the caller can tell
    the client (``month_closed: true``).
    """

    def __init__(
        self,
        repo: ILaborMonthClosureRepository,
        entry_repo: Optional[ILaborEntryRepository] = None,
    ) -> None:
        self._repo = repo
        self._entry_repo = entry_repo

    def execute(self, project_id: UUID, user_id: Optional[UUID], touches: Iterable[ClosedMonthTouch]) -> List[str]:
        touches = list(touches)
        if not touches:
            return []
        months = sorted({month_start(t.day) for t in touches})
        closed = {c.month: c for c in self._repo.list_by_project(project_id, months)}
        if not closed:
            return []
        now = datetime.now(timezone.utc)
        edits = [
            LaborMonthClosureEdit(
                id=uuid4(),
                closure_id=closed[month_start(t.day)].id,
                kind=t.kind,
                edited_at=now,
                user_id=user_id,
                entry_id=t.entry_id,
                worker_id=t.worker_id,
                entry_date=t.entry_date,
            )
            for t in touches
            if month_start(t.day) in closed
        ]
        self._repo.add_edits(edits)
        return sorted({month_start(t.day).strftime("%Y-%m") for t in touches if month_start(t.day) in closed})

    def rate_change_touches(
        self,
        project_id: UUID,
        worker_id: UUID,
        effective_date: date,
        next_effective_date: Optional[date],
        kind: str,
        rate_change_id: Optional[UUID],
    ) -> List[ClosedMonthTouch]:
        """The closed months a dated rate change reprices.

        A rate dated D applies from D until the worker's next change, so it reaches
        every closed month overlapping [D, next) in which the worker has a day.
        """
        touches: List[ClosedMonthTouch] = []
        for closure in self._repo.list_by_project(project_id):
            first, last = closure.month, month_end(closure.month)
            if last < effective_date:
                continue
            if next_effective_date is not None and first >= next_effective_date:
                continue
            if self._entry_repo is not None:
                lo = max(first, effective_date)
                hi = min(last, next_effective_date - timedelta(days=1)) if next_effective_date else last
                if not self._entry_repo.list_by_project(
                    project_id, date_from=lo, date_to=hi, worker_id=worker_id, limit=1
                ):
                    continue
            touches.append(
                ClosedMonthTouch(
                    day=first,
                    kind=kind,
                    entry_id=rate_change_id,
                    worker_id=worker_id,
                    entry_date=effective_date,
                )
            )
        return touches
