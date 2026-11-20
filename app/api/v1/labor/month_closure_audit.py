"""Record writes that land in a closed labor month ("Chốt tháng", QĐ-10).

Closing a month never blocks a write: the routes that change wages (attendance,
labor payments, rate changes) call :func:`record_closed_month_writes` after their
write succeeded. It logs who changed what in each closed month touched and returns
those months, so the route can add ``month_closed: true`` to its answer. It never
fails the request.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable, List, Optional
from uuid import UUID

from flask_jwt_extended import get_jwt_identity

from app.application.labor.labor_month_closure_usecases import ClosedMonthTouch
from wiring import get_container

_log = logging.getLogger(__name__)


def _uuid(value: Any) -> Optional[UUID]:
    if value is None or value == "":
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def touch(day: Any, kind: str, entry_id: Any = None, worker_id: Any = None, dated: bool = True):
    """A ClosedMonthTouch from loosely typed route values; None when ``day`` is missing.

    ``dated``: ``day`` is the attendance day itself (kept as the edit's date); False
    for a labor payment, whose service month is the first of the month, not a day.
    """
    d = _day(day)
    if d is None:
        return None
    return ClosedMonthTouch(
        day=d,
        kind=kind,
        entry_id=_uuid(entry_id),
        worker_id=_uuid(worker_id),
        entry_date=d if dated else None,
    )


def record_closed_month_writes(project_id: Any, touches: Iterable[Optional[ClosedMonthTouch]]) -> List[str]:
    """Log the touches that fall in closed months; return those months ("YYYY-MM")."""
    usecase = getattr(get_container(), "record_closed_month_edits_usecase", None)
    items = [t for t in touches if t is not None]
    if usecase is None or not items:
        return []
    try:
        return usecase.execute(UUID(str(project_id)), _uuid(get_jwt_identity()), items)
    except Exception:  # pragma: no cover - defensive: the write itself already succeeded
        _log.exception("recording a write into a closed labor month failed")
        return []


def rate_change_closed_month_writes(
    project_id: Any,
    worker_id: Any,
    effective_date: Any,
    next_effective_date: Any,
    kind: str,
    rate_change_id: Any = None,
) -> List[str]:
    """Record a dated rate change against every closed month it reprices."""
    usecase = getattr(get_container(), "record_closed_month_edits_usecase", None)
    eff = _day(effective_date)
    wid = _uuid(worker_id)
    if usecase is None or eff is None or wid is None:
        return []
    try:
        touches = usecase.rate_change_touches(
            UUID(str(project_id)), wid, eff, _day(next_effective_date), kind, _uuid(rate_change_id)
        )
    except Exception:  # pragma: no cover - defensive
        _log.exception("finding the closed labor months a rate change reprices failed")
        return []
    return record_closed_month_writes(project_id, touches)


def with_closed_months(payload: dict, months: List[str]) -> dict:
    """Add ``month_closed`` / ``closed_months`` to a write's answer when it touched closed months."""
    if months:
        payload["month_closed"] = True
        payload["closed_months"] = months
    return payload
