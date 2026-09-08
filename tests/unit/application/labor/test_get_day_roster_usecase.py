"""Unit tests for GetDayRosterUseCase (D3 day roster).

Covers two fixes beyond the happy path already exercised by
tests/api/test_labor_roster_endpoint.py:
  - an archived (is_active=False) worker with a labor entry logged for the
    requested day still appears on the roster; one with no entry that day
    does not.
  - a labor entry whose shift_type isn't in the known whitelist is treated
    as a full day (and a warning is logged) instead of silently zeroing hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from app.application.labor.get_day_roster_usecase import (
    GetDayRosterRequest,
    GetDayRosterUseCase,
    STATUS_ABSENT,
    STATUS_PRESENT,
)
from app.domain.entities.labor_entry import LaborEntry
from app.domain.entities.worker import Worker

_PROJECT_ID = uuid4()
_DAY = date(2026, 6, 1)


def _worker(name: str, is_active: bool = True) -> Worker:
    return Worker(
        id=uuid4(),
        project_id=_PROJECT_ID,
        name=name,
        daily_rate=Decimal("100"),
        created_at=datetime.now(timezone.utc),
        is_active=is_active,
    )


def _entry(worker_id: UUID, shift_type: Optional[str], supplement_hours: int = 0) -> LaborEntry:
    return LaborEntry(
        id=uuid4(),
        worker_id=worker_id,
        date=_DAY,
        created_at=datetime.now(timezone.utc),
        shift_type=shift_type,
        supplement_hours=supplement_hours,
    )


@dataclass
class _FakeWorkerRepo:
    workers: list

    def list_by_project(self, project_id: UUID, active_only: bool = True):
        if active_only:
            return [w for w in self.workers if w.is_active]
        return list(self.workers)


@dataclass
class _FakeEntryRepo:
    entries: list

    def list_by_project(self, project_id: UUID, date_from=None, date_to=None, **kwargs):
        return list(self.entries)


class _AlwaysAuthorizedReader:
    """AuthzReaderPort fake: caller is an admin of a resolvable company."""

    def __init__(self):
        self.company_id = uuid4()

    def company_role_for(self, user_id, company_id):
        return "admin"

    def is_assigned(self, user_id, project_id):
        return True

    def project_company_id(self, project_id):
        return self.company_id

    def primary_company_id(self, user_id):
        return self.company_id

    def admin_company_ids(self, user_id):
        return [self.company_id]

    def grants_for(self, user_id, company_id, project_id):
        return []


def _make_usecase(workers: list, entries: list) -> GetDayRosterUseCase:
    return GetDayRosterUseCase(
        worker_repo=_FakeWorkerRepo(workers),
        entry_repo=_FakeEntryRepo(entries),
        authz_reader=_AlwaysAuthorizedReader(),
    )


def _request(caller_user_id: UUID) -> GetDayRosterRequest:
    return GetDayRosterRequest(project_id=_PROJECT_ID, date=_DAY, caller_user_id=caller_user_id)


def test_archived_worker_with_entry_that_day_is_included():
    archived = _worker("Archived With Entry", is_active=False)
    entry = _entry(archived.id, shift_type="full")
    usecase = _make_usecase(workers=[archived], entries=[entry])

    rows = usecase.execute(_request(uuid4()))

    assert rows is not None
    assert len(rows) == 1
    assert rows[0].worker_id == archived.id
    assert rows[0].status == STATUS_PRESENT
    assert rows[0].hours == 8.0


def test_archived_worker_without_entry_that_day_is_excluded():
    archived = _worker("Archived No Entry", is_active=False)
    usecase = _make_usecase(workers=[archived], entries=[])

    rows = usecase.execute(_request(uuid4()))

    assert rows == []


def test_active_worker_without_entry_shows_absent():
    active = _worker("Active No Entry")
    usecase = _make_usecase(workers=[active], entries=[])

    rows = usecase.execute(_request(uuid4()))

    assert rows is not None
    assert len(rows) == 1
    assert rows[0].status == STATUS_ABSENT
    assert rows[0].hours == 0.0


def test_unknown_shift_type_treated_as_full_day(caplog):
    """A shift_type outside the known whitelist must not silently zero the hours."""
    worker = _worker("Legacy Shift")
    entry = _entry(worker.id, shift_type="legacy_double", supplement_hours=1)
    usecase = _make_usecase(workers=[worker], entries=[entry])

    with caplog.at_level("WARNING"):
        rows = usecase.execute(_request(uuid4()))

    assert rows is not None
    assert len(rows) == 1
    # full-day base (8h) + the 1 supplement hour, not the 0 an unrecognised
    # shift_type would produce via a naive dict.get(..., 0) fallback.
    assert rows[0].hours == 9.0
    assert rows[0].day_type == "legacy_double"
    assert any("legacy_double" in record.message for record in caplog.records)


def test_known_shift_types_unaffected():
    worker_full = _worker("Full")
    worker_half = _worker("Half")
    entries = [_entry(worker_full.id, shift_type="full"), _entry(worker_half.id, shift_type="half")]
    usecase = _make_usecase(workers=[worker_full, worker_half], entries=entries)

    rows = usecase.execute(_request(uuid4()))

    by_id = {r.worker_id: r for r in rows}
    assert by_id[worker_full.id].hours == 8.0
    assert by_id[worker_half.id].hours == 4.0
