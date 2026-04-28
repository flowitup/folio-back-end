"""Unit tests for LaborEntry domain entity.

Covers: construction (valid / invalid), __post_init__ validators,
and effective_cost() semantics for supplement-hours feature.
"""

import pytest
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from app.domain.entities.labor_entry import LaborEntry
from app.domain.exceptions.labor_exceptions import InvalidLaborEntryError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**kwargs) -> LaborEntry:
    """Build a LaborEntry with sensible defaults; callers may override any field."""
    defaults = dict(
        id=uuid4(),
        worker_id=uuid4(),
        date=date(2026, 1, 15),
        created_at=datetime.now(timezone.utc),
        shift_type="full",
        supplement_hours=0,
        amount_override=None,
        note=None,
    )
    defaults.update(kwargs)
    return LaborEntry(**defaults)


DAILY_RATE = Decimal("100.00")


# ---------------------------------------------------------------------------
# Construction: valid scenarios
# ---------------------------------------------------------------------------


class TestLaborEntryValidConstruction:
    def test_full_shift_no_supplement(self):
        entry = _make_entry(shift_type="full", supplement_hours=0)
        assert entry.shift_type == "full"
        assert entry.supplement_hours == 0

    def test_half_shift_no_supplement(self):
        entry = _make_entry(shift_type="half", supplement_hours=0)
        assert entry.shift_type == "half"

    def test_overtime_shift_no_supplement(self):
        entry = _make_entry(shift_type="overtime", supplement_hours=0)
        assert entry.shift_type == "overtime"

    def test_full_shift_with_supplement(self):
        entry = _make_entry(shift_type="full", supplement_hours=3)
        assert entry.supplement_hours == 3

    def test_supplement_only_row(self):
        """shift_type=None + supplement_hours>0 is a valid standalone entry."""
        entry = _make_entry(shift_type=None, supplement_hours=3)
        assert entry.shift_type is None
        assert entry.supplement_hours == 3

    def test_supplement_at_max_boundary(self):
        entry = _make_entry(shift_type=None, supplement_hours=12)
        assert entry.supplement_hours == 12

    def test_supplement_at_min_boundary_with_shift(self):
        entry = _make_entry(shift_type="full", supplement_hours=0)
        assert entry.supplement_hours == 0

    def test_full_shift_with_override(self):
        entry = _make_entry(shift_type="full", amount_override=Decimal("200.00"))
        assert entry.amount_override == Decimal("200.00")


# ---------------------------------------------------------------------------
# Construction: invalid scenarios (InvalidLaborEntryError)
# ---------------------------------------------------------------------------


class TestLaborEntryInvalidConstruction:
    def test_empty_row_raises(self):
        """shift_type=None AND supplement_hours=0 must be rejected."""
        with pytest.raises(InvalidLaborEntryError, match="Empty entry"):
            _make_entry(shift_type=None, supplement_hours=0)

    def test_supplement_hours_negative_raises(self):
        with pytest.raises(InvalidLaborEntryError, match="0..12"):
            _make_entry(shift_type="full", supplement_hours=-1)

    def test_supplement_hours_above_cap_raises(self):
        with pytest.raises(InvalidLaborEntryError, match="0..12"):
            _make_entry(shift_type=None, supplement_hours=13)

    def test_supplement_hours_non_integer_raises(self):
        with pytest.raises(InvalidLaborEntryError, match="integer"):
            _make_entry(shift_type="full", supplement_hours=1.5)  # type: ignore[arg-type]

    def test_override_without_shift_raises(self):
        """amount_override is meaningless without a shift_type."""
        with pytest.raises(InvalidLaborEntryError, match="amount_override requires"):
            _make_entry(shift_type=None, supplement_hours=3, amount_override=Decimal("100"))


# ---------------------------------------------------------------------------
# effective_cost()
# ---------------------------------------------------------------------------


class TestEffectiveCost:
    def test_null_shift_returns_zero(self):
        entry = _make_entry(shift_type=None, supplement_hours=4)
        assert entry.effective_cost(DAILY_RATE) == Decimal("0")

    def test_full_shift_no_override(self):
        entry = _make_entry(shift_type="full", supplement_hours=0)
        assert entry.effective_cost(DAILY_RATE) == Decimal("100.00")

    def test_half_shift_no_override(self):
        entry = _make_entry(shift_type="half", supplement_hours=0)
        assert entry.effective_cost(DAILY_RATE) == Decimal("50.0")

    def test_overtime_shift_no_override(self):
        entry = _make_entry(shift_type="overtime", supplement_hours=0)
        assert entry.effective_cost(DAILY_RATE) == Decimal("150.0")

    def test_override_wins_over_multiplier(self):
        entry = _make_entry(shift_type="full", amount_override=Decimal("250.00"))
        assert entry.effective_cost(DAILY_RATE) == Decimal("250.00")

    def test_override_wins_for_half_shift(self):
        entry = _make_entry(shift_type="half", amount_override=Decimal("75.00"))
        assert entry.effective_cost(DAILY_RATE) == Decimal("75.00")
