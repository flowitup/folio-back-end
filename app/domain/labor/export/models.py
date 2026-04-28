"""DTOs for labor export: ExportFormat, ExportRange, ExportContext, MonthBucket."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Literal
from uuid import UUID

from app.application.labor.get_labor_summary import LaborSummaryResponse
from app.application.labor.list_labor_entries import LaborEntryDetail

# Literal type alias — not a class, so no instantiation
ExportFormat = Literal["xlsx", "pdf"]


@dataclass(frozen=True)
class ExportRange:
    """Contiguous month range for export (both ends inclusive, first day of month)."""

    from_month: date  # first day of from-month  e.g. date(2026, 1, 1)
    to_month: date  # first day of to-month    e.g. date(2026, 3, 1)


@dataclass(frozen=True)
class ExportContext:
    """Metadata carried through the export pipeline."""

    project_name: str
    project_id: UUID
    range: ExportRange
    generated_at: datetime
    generated_by_email: str


@dataclass(frozen=True)
class MonthBucket:
    """All data for a single calendar month within an export range."""

    month: date  # first day of the month
    summary: LaborSummaryResponse
    daily_entries: List[LaborEntryDetail]
