"""Unit tests for Pydantic schema validation in labor API.

Covers model_validator error branches for LogAttendanceRequest and
UpdateAttendanceRequest (supplement_hours bounds, empty-row, override-without-shift).
"""

import pytest
from pydantic import ValidationError

from app.api.v1.labor.schemas import LogAttendanceRequest, UpdateAttendanceRequest


# ---------------------------------------------------------------------------
# LogAttendanceRequest — field-level bounds
# ---------------------------------------------------------------------------


class TestLogAttendanceSchemaFieldBounds:
    def test_supplement_hours_negative_raises_422(self):
        with pytest.raises(ValidationError) as exc_info:
            LogAttendanceRequest(
                worker_id="00000000-0000-0000-0000-000000000001",
                date="2026-01-01",
                shift_type="full",
                supplement_hours=-1,
            )
        errors = exc_info.value.errors()
        assert any("supplement_hours" in str(e["loc"]) for e in errors)

    def test_supplement_hours_above_cap_raises_422(self):
        with pytest.raises(ValidationError) as exc_info:
            LogAttendanceRequest(
                worker_id="00000000-0000-0000-0000-000000000001",
                date="2026-01-01",
                shift_type="full",
                supplement_hours=13,
            )
        errors = exc_info.value.errors()
        assert any("supplement_hours" in str(e["loc"]) for e in errors)

    def test_supplement_hours_at_zero_with_shift_ok(self):
        req = LogAttendanceRequest(
            worker_id="00000000-0000-0000-0000-000000000001",
            date="2026-01-01",
            shift_type="full",
            supplement_hours=0,
        )
        assert req.supplement_hours == 0

    def test_supplement_hours_at_twelve_with_shift_ok(self):
        req = LogAttendanceRequest(
            worker_id="00000000-0000-0000-0000-000000000001",
            date="2026-01-01",
            shift_type="full",
            supplement_hours=12,
        )
        assert req.supplement_hours == 12


# ---------------------------------------------------------------------------
# LogAttendanceRequest — model_validator paths
# ---------------------------------------------------------------------------


class TestLogAttendanceModelValidator:
    def test_empty_row_raises_422(self):
        """shift_type=None AND supplement_hours=0 → model_validator raises."""
        with pytest.raises(ValidationError) as exc_info:
            LogAttendanceRequest(
                worker_id="00000000-0000-0000-0000-000000000001",
                date="2026-01-01",
                shift_type=None,
                supplement_hours=0,
            )
        # Should mention the empty entry constraint
        errors_str = str(exc_info.value)
        assert "Empty entry" in errors_str or "supplement_hours" in errors_str

    def test_override_without_shift_raises_422(self):
        """shift_type=None + amount_override set → model_validator raises."""
        with pytest.raises(ValidationError) as exc_info:
            LogAttendanceRequest(
                worker_id="00000000-0000-0000-0000-000000000001",
                date="2026-01-01",
                shift_type=None,
                supplement_hours=5,
                amount_override=50.0,
            )
        errors_str = str(exc_info.value)
        assert "amount_override" in errors_str

    def test_supplement_only_valid(self):
        """shift_type=None + supplement_hours=5 → valid."""
        req = LogAttendanceRequest(
            worker_id="00000000-0000-0000-0000-000000000001",
            date="2026-01-01",
            shift_type=None,
            supplement_hours=5,
        )
        assert req.shift_type is None
        assert req.supplement_hours == 5

    def test_shift_with_supplement_valid(self):
        """shift_type set + supplement_hours > 0 → valid combination."""
        req = LogAttendanceRequest(
            worker_id="00000000-0000-0000-0000-000000000001",
            date="2026-01-01",
            shift_type="full",
            supplement_hours=3,
        )
        assert req.shift_type == "full"
        assert req.supplement_hours == 3


# ---------------------------------------------------------------------------
# UpdateAttendanceRequest — model_validator override-consistency
# ---------------------------------------------------------------------------


class TestUpdateAttendanceModelValidator:
    def test_clear_shift_with_override_raises_422(self):
        """Explicitly null shift_type + amount_override → model_validator raises."""
        with pytest.raises(ValidationError) as exc_info:
            UpdateAttendanceRequest(
                shift_type=None,
                amount_override=100.0,
            )
        errors_str = str(exc_info.value)
        assert "amount_override" in errors_str

    def test_partial_update_no_shift_no_override_ok(self):
        """Typical PATCH: only supplement_hours, no shift, no override."""
        req = UpdateAttendanceRequest(supplement_hours=6)
        assert req.supplement_hours == 6
        assert req.shift_type is None
        assert req.amount_override is None

    def test_supplement_hours_at_boundary_ok(self):
        req = UpdateAttendanceRequest(supplement_hours=12)
        assert req.supplement_hours == 12

    def test_supplement_hours_above_cap_raises_422(self):
        with pytest.raises(ValidationError):
            UpdateAttendanceRequest(supplement_hours=13)
