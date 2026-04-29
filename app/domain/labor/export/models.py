"""DTOs for labor export: ExportFormat, ExportRange, ExportContext, MonthBucket."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional
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
    """Metadata carried through the export pipeline.

    Single-worker mode is indicated by worker_name being set.
    worker_daily_rate carries the worker's base daily rate for display in headers.
    """

    project_name: str
    project_id: UUID
    range: ExportRange
    generated_at: datetime
    generated_by_email: str
    # Optional single-worker scope — None means project-wide export
    worker_name: Optional[str] = field(default=None)
    worker_daily_rate: Optional[Decimal] = field(default=None)


@dataclass(frozen=True)
class MonthBucket:
    """All data for a single calendar month within an export range."""

    month: date  # first day of the month
    summary: LaborSummaryResponse
    daily_entries: List[LaborEntryDetail]
