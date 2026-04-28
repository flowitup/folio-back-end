"""Unit tests for xlsx_builder.build_xlsx.

Tests use openpyxl.load_workbook round-trip to assert cell values and formats.
Fixtures hand-construct LaborSummaryResponse / LaborEntryDetail without DB access.
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from uuid import uuid4

import pytest

from app.application.labor.get_labor_summary import LaborSummaryResponse, WorkerCostSummary
from app.application.labor.list_labor_entries import LaborEntryDetail
from app.domain.labor.export.models import ExportContext, ExportRange, MonthBucket
from app.domain.labor.export.xlsx_builder import EUR_FR_FORMAT, build_xlsx


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_context(from_month: date | None = None, to_month: date | None = None) -> ExportContext:
    return ExportContext(
        project_name="Downtown Office Tower",
        project_id=uuid4(),
        range=ExportRange(
            from_month=from_month or date(2026, 4, 1),
            to_month=to_month or date(2026, 5, 31),
        ),
        generated_at=datetime(2026, 4, 28, 17, 0, 0),
        generated_by_email="admin@example.com",
    )


def _make_worker_summary(
    *,
    worker_id: str = "w1",
    worker_name: str = "Antoine",
    days_worked: int = 10,
    total_cost: float = 2000.0,
    banked_hours: int = 8,
    bonus_full_days: int = 1,
    bonus_half_days: int = 0,
    bonus_cost: float = 200.0,
) -> WorkerCostSummary:
    return WorkerCostSummary(
        worker_id=worker_id,
        worker_name=worker_name,
        days_worked=days_worked,
        total_cost=total_cost,
        banked_hours=banked_hours,
        bonus_full_days=bonus_full_days,
        bonus_half_days=bonus_half_days,
        bonus_cost=bonus_cost,
    )


def _make_summary_response(*workers: WorkerCostSummary) -> LaborSummaryResponse:
    rows = list(workers)
    return LaborSummaryResponse(
        rows=rows,
        total_days=sum(r.days_worked for r in rows),
        total_cost=sum(r.total_cost for r in rows),
        total_banked_hours=sum(r.banked_hours for r in rows),
        total_bonus_days=sum(r.bonus_full_days + r.bonus_half_days * 0.5 for r in rows),
        total_bonus_cost=sum(r.bonus_cost for r in rows),
    )


def _make_entry(
    *,
    worker_id: str = "w1",
    worker_name: str = "Antoine",
    entry_date: str = "2026-04-01",
    shift_type: str = "full",
    supplement_hours: int = 0,
    effective_cost: float = 200.0,
    amount_override: float | None = None,
    note: str | None = None,
) -> LaborEntryDetail:
    return LaborEntryDetail(
        id=str(uuid4()),
        worker_id=worker_id,
        worker_name=worker_name,
        date=entry_date,
        amount_override=amount_override,
        effective_cost=effective_cost,
        note=note,
        shift_type=shift_type,
        supplement_hours=supplement_hours,
        created_at="2026-04-28T17:00:00",
    )


def _make_two_month_buckets() -> tuple[ExportContext, list[MonthBucket]]:
    """Two-month fixture: Apr 2026 + May 2026, same worker in both."""
    worker_apr = _make_worker_summary(
        worker_id="w1",
        worker_name="Antoine",
        days_worked=10,
        total_cost=2200.0,
        banked_hours=8,
        bonus_full_days=1,
        bonus_half_days=0,
        bonus_cost=200.0,
    )
    worker_may = _make_worker_summary(
        worker_id="w1",
        worker_name="Antoine",
        days_worked=5,
        total_cost=1100.0,
        banked_hours=0,
        bonus_full_days=0,
        bonus_half_days=0,
        bonus_cost=0.0,
    )
    entry_apr = _make_entry(worker_id="w1", worker_name="Antoine", entry_date="2026-04-01")
    entry_may = _make_entry(worker_id="w1", worker_name="Antoine", entry_date="2026-05-01")

    bucket_apr = MonthBucket(
        month=date(2026, 4, 1),
        summary=_make_summary_response(worker_apr),
        daily_entries=[entry_apr],
    )
    bucket_may = MonthBucket(
        month=date(2026, 5, 1),
        summary=_make_summary_response(worker_may),
        daily_entries=[entry_may],
    )
    ctx = _make_context(from_month=date(2026, 4, 1), to_month=date(2026, 5, 31))
    return ctx, [bucket_apr, bucket_may]


def _load(raw: bytes):
    """Load workbook from bytes for assertions."""
    return __import__("openpyxl").load_workbook(BytesIO(raw), data_only=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildXlsxReturnType:
    def test_build_xlsx_minimum_returns_bytes_with_pk_magic(self):
        """build_xlsx must return bytes starting with PK (xlsx is ZIP)."""
        ctx, buckets = _make_two_month_buckets()
        result = build_xlsx(ctx, buckets)
        assert isinstance(result, bytes)
        assert result[:2] == b"PK", "xlsx must start with ZIP magic bytes PK"


class TestSummarySheetStructure:
    def test_build_xlsx_summary_sheet_structure(self):
        """Sheet names must start with Summary, Apr 2026, May 2026."""
        ctx, buckets = _make_two_month_buckets()
        wb = _load(build_xlsx(ctx, buckets))
        assert wb.sheetnames[:3] == ["Summary", "Apr 2026", "May 2026"]

    def test_build_xlsx_summary_header_cells(self):
        """A1..A4 must contain expected header strings."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        assert ws["A1"].value == "Folio · Labor Export"
        assert "Downtown Office Tower" in ws["A2"].value
        assert "Apr 2026" in ws["A3"].value
        assert "May 2026" in ws["A3"].value
        assert "admin@example.com" in ws["A4"].value

    def test_build_xlsx_summary_table_headers_in_row_6(self):
        """Row 6 of Summary must contain all expected column headers."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        headers = [ws.cell(row=6, column=c).value for c in range(1, 9)]
        assert headers[0] == "Worker"
        assert headers[1] == "Days"
        assert headers[5] == "Priced cost"
        assert headers[6] == "Bonus cost"
        assert headers[7] == "Total (priced + bonus)"


class TestAggregation:
    def test_build_xlsx_summary_aggregates_across_months(self):
        """Worker present in 2 buckets must show summed days and total_cost in Summary."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        # Row 7 = first (and only) worker row in Summary
        worker_cell = ws.cell(row=7, column=1).value
        days_cell = ws.cell(row=7, column=2).value
        total_cell = ws.cell(row=7, column=8).value

        assert worker_cell == "Antoine"
        # Apr: 10 days, May: 5 days → 15 total
        assert days_cell == 15
        # Apr: 2200.0, May: 1100.0 → 3300.0 total
        assert abs(total_cell - 3300.0) < 0.01

    def test_build_xlsx_summary_footer_totals_row(self):
        """Footer row (TOTAL) must sum all worker values."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        # Row 7 = data row, Row 8 = footer (1 worker only)
        footer_label = ws.cell(row=8, column=1).value
        footer_days = ws.cell(row=8, column=2).value
        assert footer_label == "TOTAL"
        assert footer_days == 15


class TestCurrencyFormat:
    def test_build_xlsx_currency_number_format_applied(self):
        """Priced cost cell (column F = 6) in row 7 must use EUR_FR_FORMAT."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        cell = ws.cell(row=7, column=6)  # Priced cost
        assert cell.number_format == EUR_FR_FORMAT

    def test_build_xlsx_currency_value_is_number_not_string(self):
        """Currency cells must hold float/int, not a formatted string."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Summary"]
        priced_cost_cell = ws.cell(row=7, column=6)
        assert isinstance(
            priced_cost_cell.value, (float, int)
        ), f"Expected numeric value, got {type(priced_cost_cell.value)}: {priced_cost_cell.value!r}"


class TestMonthlySheetSections:
    def test_build_xlsx_per_month_sheet_has_summary_and_detail_sections(self):
        """Apr 2026 sheet must contain both a per-worker summary table and a Daily detail section."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Apr 2026"]

        # Collect all cell values
        all_values = [ws.cell(row=r, column=1).value for r in range(1, 30)]

        # Row 6 should be "Worker" header (start of summary table)
        assert ws.cell(row=6, column=1).value == "Worker"

        # "Daily detail" section label must appear somewhere after row 6
        assert "Daily detail" in all_values, f"'Daily detail' not found in column A rows 1-29. Values: {all_values}"

    def test_build_xlsx_daily_detail_has_correct_headers(self):
        """Daily detail table in a month sheet must have Date/Worker/Shift/... headers."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Apr 2026"]

        # Find the "Daily detail" row and check the next row's headers
        detail_section_row = None
        for r in range(1, 40):
            if ws.cell(row=r, column=1).value == "Daily detail":
                detail_section_row = r
                break

        assert detail_section_row is not None, "Daily detail section not found"
        hdr_row = detail_section_row + 1
        headers = [ws.cell(row=hdr_row, column=c).value for c in range(1, 8)]
        assert headers[0] == "Date"
        assert headers[1] == "Worker"
        assert headers[2] == "Shift"
        assert headers[5] == "Effective cost"

    def test_build_xlsx_daily_detail_effective_cost_has_eur_format(self):
        """Effective cost column in daily detail must use EUR_FR_FORMAT."""
        ctx, buckets = _make_two_month_buckets()
        ws = _load(build_xlsx(ctx, buckets))["Apr 2026"]

        # Find daily detail header row
        detail_section_row = None
        for r in range(1, 40):
            if ws.cell(row=r, column=1).value == "Daily detail":
                detail_section_row = r
                break
        assert detail_section_row is not None

        data_row = detail_section_row + 2  # header + 1 = first data row
        eff_cost_cell = ws.cell(row=data_row, column=6)  # Effective cost = col 6
        assert eff_cost_cell.number_format == EUR_FR_FORMAT


class TestEmptyRange:
    def test_build_xlsx_empty_range(self):
        """Empty buckets → only Summary sheet with 'No labor entries' message."""
        ctx = _make_context(from_month=date(2026, 4, 1), to_month=date(2026, 6, 30))
        empty_buckets = [
            MonthBucket(
                month=date(2026, 4, 1),
                summary=_make_summary_response(),
                daily_entries=[],
            )
        ]
        wb = _load(build_xlsx(ctx, empty_buckets))
        assert wb.sheetnames == ["Summary"], f"Expected only Summary sheet, got {wb.sheetnames}"
        ws = wb["Summary"]
        a6 = ws["A6"].value
        assert a6 is not None
        assert "No labor entries" in a6
        assert "Apr 2026" in a6

    def test_build_xlsx_empty_range_no_buckets(self):
        """Zero buckets → Summary sheet with 'No labor entries' message."""
        ctx = _make_context(from_month=date(2026, 4, 1), to_month=date(2026, 4, 30))
        wb = _load(build_xlsx(ctx, []))
        assert wb.sheetnames == ["Summary"]
        assert "No labor entries" in (wb["Summary"]["A6"].value or "")


class TestColumnLabels:
    def test_build_xlsx_no_aggregated_total_alone(self):
        """Total column header must be 'Total (priced + bonus)', never just 'Total'."""
        ctx, buckets = _make_two_month_buckets()
        wb = _load(build_xlsx(ctx, buckets))
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value == "Total":
                        pytest.fail(
                            f"Found bare 'Total' header in sheet '{sheet_name}' "
                            f"at {cell.coordinate} — must be 'Total (priced + bonus)'"
                        )

    def test_build_xlsx_no_avg_cost_per_day_column_anywhere(self):
        """No sheet should contain 'Avg cost' anywhere (reviewer HIGH-3 carry-forward)."""
        ctx, buckets = _make_two_month_buckets()
        wb = _load(build_xlsx(ctx, buckets))
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and "Avg cost" in cell.value:
                        pytest.fail(f"Found 'Avg cost' in sheet '{sheet_name}' at {cell.coordinate}")

    def test_build_xlsx_total_column_label_is_exact(self):
        """Assert exact text of the last header column across all summary tables."""
        ctx, buckets = _make_two_month_buckets()
        wb = _load(build_xlsx(ctx, buckets))
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            # Row 6 is the summary table header in all non-empty sheets
            last_header = ws.cell(row=6, column=8).value
            if last_header is not None:
                assert last_header == "Total (priced + bonus)", (
                    f"Sheet '{sheet_name}' row 6 col H: expected 'Total (priced + bonus)', " f"got {last_header!r}"
                )


class TestMultipleWorkers:
    def test_build_xlsx_two_workers_in_same_bucket(self):
        """Two workers in the same bucket appear as two rows in Summary + month sheet."""
        w1 = _make_worker_summary(
            worker_id="w1", worker_name="Antoine", days_worked=5, total_cost=1000.0, bonus_cost=100.0
        )
        w2 = _make_worker_summary(worker_id="w2", worker_name="Bảo", days_worked=3, total_cost=600.0, bonus_cost=0.0)
        bucket = MonthBucket(
            month=date(2026, 4, 1),
            summary=_make_summary_response(w1, w2),
            daily_entries=[
                _make_entry(worker_id="w1", worker_name="Antoine"),
                _make_entry(worker_id="w2", worker_name="Bảo"),
            ],
        )
        ctx = _make_context(from_month=date(2026, 4, 1), to_month=date(2026, 4, 30))
        wb = _load(build_xlsx(ctx, [bucket]))

        ws = wb["Summary"]
        # Rows 7 and 8 = workers (sorted by name: Antoine < Bảo)
        assert ws.cell(row=7, column=1).value == "Antoine"
        assert ws.cell(row=8, column=1).value == "Bảo"
        # Row 9 = TOTAL footer
        assert ws.cell(row=9, column=1).value == "TOTAL"
