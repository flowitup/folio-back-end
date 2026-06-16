"""Unit tests for WorkerRateChange domain entity and rate-resolution logic."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.worker_rate_change import WorkerRateChange
from app.domain.exceptions.labor_exceptions import InvalidRateChangeError


# ---------------------------------------------------------------------------
# Entity invariants
# ---------------------------------------------------------------------------


def _make_rc(daily_rate=Decimal("100"), effective_date=date(2026, 1, 1)):
    return WorkerRateChange(
        id=uuid4(),
        worker_id=uuid4(),
        effective_date=effective_date,
        daily_rate=daily_rate,
        created_at=datetime.now(timezone.utc),
    )


def test_rate_change_valid():
    rc = _make_rc(daily_rate=Decimal("150.50"))
    assert rc.daily_rate == Decimal("150.50")


def test_rate_change_zero_raises():
    with pytest.raises(InvalidRateChangeError):
        _make_rc(daily_rate=Decimal("0"))


def test_rate_change_negative_raises():
    with pytest.raises(InvalidRateChangeError):
        _make_rc(daily_rate=Decimal("-1"))


def test_rate_change_none_raises():
    with pytest.raises((InvalidRateChangeError, TypeError)):
        WorkerRateChange(
            id=uuid4(),
            worker_id=uuid4(),
            effective_date=date(2026, 1, 1),
            daily_rate=None,  # type: ignore[arg-type]
            created_at=datetime.now(timezone.utc),
        )


def test_rate_change_equality_by_id():
    shared_id = uuid4()
    rc1 = WorkerRateChange(
        id=shared_id,
        worker_id=uuid4(),
        effective_date=date(2026, 1, 1),
        daily_rate=Decimal("100"),
        created_at=datetime.now(timezone.utc),
    )
    rc2 = WorkerRateChange(
        id=shared_id,
        worker_id=uuid4(),
        effective_date=date(2026, 6, 1),
        daily_rate=Decimal("200"),
        created_at=datetime.now(timezone.utc),
    )
    assert rc1 == rc2
    assert hash(rc1) == hash(rc2)


def test_rate_change_different_ids_not_equal():
    rc1 = _make_rc()
    rc2 = _make_rc()
    assert rc1 != rc2


# ---------------------------------------------------------------------------
# _resolve_rate helper (unit-level, no DB)
# ---------------------------------------------------------------------------


def test_resolve_rate_no_changes_returns_base_rate():
    """When no rate changes exist, the worker's base daily_rate is used."""
    from app.application.labor.list_labor_entries import _resolve_rate
    from app.domain.entities.worker import Worker

    worker = Worker(
        id=uuid4(),
        project_id=uuid4(),
        name="Alice",
        daily_rate=Decimal("100"),
        created_at=datetime.now(timezone.utc),
    )
    rate = _resolve_rate(worker, date(2026, 6, 15), [])
    assert rate == Decimal("100")


def _make_worker_with_rate(daily_rate: Decimal):
    from app.domain.entities.worker import Worker

    return Worker(
        id=uuid4(),
        project_id=uuid4(),
        name="Bob",
        daily_rate=daily_rate,
        created_at=datetime.now(timezone.utc),
    )


def test_resolve_rate_exact_effective_date():
    """Entry on the exact effective_date uses the new rate."""
    from app.application.labor.list_labor_entries import _resolve_rate

    worker = _make_worker_with_rate(Decimal("100"))
    rc = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2026, 6, 10),
        daily_rate=Decimal("150"),
        created_at=datetime.now(timezone.utc),
    )
    # On D: new rate
    assert _resolve_rate(worker, date(2026, 6, 10), [rc]) == Decimal("150")


def test_resolve_rate_before_effective_date_uses_base():
    """Entry before effective_date still uses the old (base) rate."""
    from app.application.labor.list_labor_entries import _resolve_rate

    worker = _make_worker_with_rate(Decimal("100"))
    rc = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2026, 6, 10),
        daily_rate=Decimal("150"),
        created_at=datetime.now(timezone.utc),
    )
    # On D-1: base rate
    assert _resolve_rate(worker, date(2026, 6, 9), [rc]) == Decimal("100")


def test_resolve_rate_after_effective_date_uses_new():
    """Entry after effective_date uses the new rate."""
    from app.application.labor.list_labor_entries import _resolve_rate

    worker = _make_worker_with_rate(Decimal("100"))
    rc = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2026, 6, 10),
        daily_rate=Decimal("150"),
        created_at=datetime.now(timezone.utc),
    )
    # On D+1: new rate
    assert _resolve_rate(worker, date(2026, 6, 11), [rc]) == Decimal("150")


def test_resolve_rate_multiple_changes_picks_latest_applicable():
    """Multiple changes: the one with the greatest effective_date <= D wins."""
    from app.application.labor.list_labor_entries import _resolve_rate

    worker = _make_worker_with_rate(Decimal("100"))
    rc_early = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2026, 3, 1),
        daily_rate=Decimal("120"),
        created_at=datetime.now(timezone.utc),
    )
    rc_late = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2026, 6, 1),
        daily_rate=Decimal("200"),
        created_at=datetime.now(timezone.utc),
    )
    # List must be DESC (as returned by the repository)
    changes_desc = [rc_late, rc_early]

    # Before first change → base
    assert _resolve_rate(worker, date(2026, 2, 28), changes_desc) == Decimal("100")
    # On first change → 120
    assert _resolve_rate(worker, date(2026, 3, 1), changes_desc) == Decimal("120")
    # Between changes → 120
    assert _resolve_rate(worker, date(2026, 5, 31), changes_desc) == Decimal("120")
    # On second change → 200
    assert _resolve_rate(worker, date(2026, 6, 1), changes_desc) == Decimal("200")
    # After second change → 200
    assert _resolve_rate(worker, date(2026, 7, 1), changes_desc) == Decimal("200")


def test_resolve_rate_future_change_does_not_affect_earlier_entry():
    """A future-dated change must NOT raise the cost of an entry logged today."""
    from app.application.labor.list_labor_entries import _resolve_rate

    worker = _make_worker_with_rate(Decimal("100"))
    future_rc = WorkerRateChange(
        id=uuid4(),
        worker_id=worker.id,
        effective_date=date(2027, 1, 1),
        daily_rate=Decimal("999"),
        created_at=datetime.now(timezone.utc),
    )
    today = date(2026, 6, 16)
    assert _resolve_rate(worker, today, [future_rc]) == Decimal("100")
