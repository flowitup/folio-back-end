"""Unit tests for YYYY-MM regex tightening on export query schemas (MED-2).

Verifies that years outside 1900-2199 are rejected by both ExportInvoicesQuery
and ExportLaborQuery, and that valid boundary years are accepted.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.invoices.schemas import ExportInvoicesQuery
from app.api.v1.labor.schemas import ExportLaborQuery


# ---------------------------------------------------------------------------
# ExportInvoicesQuery — YYYY-MM regex
# ---------------------------------------------------------------------------


class TestExportInvoicesQueryYearRange:
    def test_year_0000_rejected(self):
        """Year 0000 is outside 1900-2199 range → ValidationError."""
        with pytest.raises(ValidationError):
            ExportInvoicesQuery.model_validate({"from": "0000-01", "to": "2026-01", "format": "xlsx"})

    def test_year_1899_rejected(self):
        """Year 1899 is below 1900 floor → ValidationError."""
        with pytest.raises(ValidationError):
            ExportInvoicesQuery.model_validate({"from": "1899-12", "to": "2026-01", "format": "xlsx"})

    def test_year_2200_rejected(self):
        """Year 2200 is above 2199 ceiling → ValidationError."""
        with pytest.raises(ValidationError):
            ExportInvoicesQuery.model_validate({"from": "2026-01", "to": "2200-01", "format": "xlsx"})

    def test_year_1900_accepted(self):
        """Year 1900 is the lower boundary — must be accepted."""
        q = ExportInvoicesQuery.model_validate({"from": "1900-01", "to": "1900-02", "format": "xlsx"})
        assert q.from_month == "1900-01"

    def test_year_2199_accepted(self):
        """Year 2199 is the upper boundary — must be accepted."""
        q = ExportInvoicesQuery.model_validate({"from": "2199-11", "to": "2199-12", "format": "xlsx"})
        assert q.to_month == "2199-12"

    def test_current_year_accepted(self):
        """Typical current year (2026) must be accepted."""
        q = ExportInvoicesQuery.model_validate({"from": "2026-01", "to": "2026-01", "format": "pdf"})
        assert q.from_month == "2026-01"


# ---------------------------------------------------------------------------
# ExportLaborQuery — YYYY-MM regex
# ---------------------------------------------------------------------------


class TestExportLaborQueryYearRange:
    def test_year_0000_rejected(self):
        """Year 0000 is outside 1900-2199 range → ValidationError."""
        with pytest.raises(ValidationError):
            ExportLaborQuery.model_validate({"from": "0000-01", "to": "2026-01", "format": "xlsx"})

    def test_year_1899_rejected(self):
        """Year 1899 is below 1900 floor → ValidationError."""
        with pytest.raises(ValidationError):
            ExportLaborQuery.model_validate({"from": "1899-12", "to": "2026-01", "format": "xlsx"})

    def test_year_2200_rejected(self):
        """Year 2200 is above 2199 ceiling → ValidationError."""
        with pytest.raises(ValidationError):
            ExportLaborQuery.model_validate({"from": "2026-01", "to": "2200-01", "format": "xlsx"})

    def test_year_1900_accepted(self):
        """Year 1900 is the lower boundary — must be accepted."""
        q = ExportLaborQuery.model_validate({"from": "1900-01", "to": "1900-02", "format": "xlsx"})
        assert q.from_month == "1900-01"

    def test_year_2199_accepted(self):
        """Year 2199 is the upper boundary — must be accepted."""
        q = ExportLaborQuery.model_validate({"from": "2199-11", "to": "2199-12", "format": "xlsx"})
        assert q.to_month == "2199-12"

    def test_current_year_accepted(self):
        """Typical current year (2026) must be accepted."""
        q = ExportLaborQuery.model_validate({"from": "2026-01", "to": "2026-01", "format": "pdf"})
        assert q.from_month == "2026-01"
