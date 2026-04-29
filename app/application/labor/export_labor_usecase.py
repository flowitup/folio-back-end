"""ExportLaborUseCase — orchestrates multi-month summary + daily entries → file bytes."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional
from uuid import UUID

from app.application.labor.get_labor_summary import GetLaborSummaryUseCase, GetLaborSummaryRequest
from app.application.labor.list_labor_entries import ListLaborEntriesUseCase, ListLaborEntriesRequest
from app.application.labor.ports import IWorkerRepository, ILaborEntryRepository
from app.application.projects.ports import IProjectRepository
from app.domain.exceptions.labor_exceptions import WorkerNotFoundError
from app.domain.exceptions.project_exceptions import ProjectNotFoundError
from app.domain.labor.export.models import ExportContext, ExportFormat, ExportRange, MonthBucket


@dataclass
class ExportLaborRequest:
    """Input DTO for the export use case."""

    project_id: UUID
    from_month: str  # YYYY-MM  e.g. "2026-01"
    to_month: str  # YYYY-MM  e.g. "2026-03"
    format: ExportFormat  # "xlsx" | "pdf"
    acting_user_email: str
    # Optional: when set, scopes entire export to a single worker
    worker_id: Optional[UUID] = field(default=None)


@dataclass
class ExportLaborResult:
    """Output DTO returned to the API layer."""

    content: bytes
    filename: str  # e.g. "labor-downtown-office-tower-2026-01-to-2026-03.xlsx"
    mime_type: str  # e.g. "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _parse_yyyy_mm(s: str) -> date:
    """Parse 'YYYY-MM' string to date with day=1."""
    y, m = int(s[:4]), int(s[5:7])
    return date(y, m, 1)


def _enumerate_months(from_d: date, to_d: date) -> List[date]:
    """Return list of first-of-month dates, inclusive on both ends."""
    months: List[date] = []
    cur = from_d.replace(day=1)
    end = to_d.replace(day=1)
    while cur <= end:
        months.append(cur)
        # Advance to next month
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return months


def _month_bounds(m: date) -> tuple[date, date]:
    """Return (first_day, last_day) for the month of the given date."""
    first = m.replace(day=1)
    last_day = calendar.monthrange(m.year, m.month)[1]
    last = date(m.year, m.month, last_day)
    return first, last


class ExportLaborUseCase:
    """Orchestrate multi-month labor data into a downloadable file.

    Permission check is delegated to the route layer (@require_permission("project:read")).
    This use-case only validates project existence.
    """

    def __init__(
        self,
        worker_repo: IWorkerRepository,
        entry_repo: ILaborEntryRepository,
        summary_usecase: GetLaborSummaryUseCase,
        list_entries_usecase: ListLaborEntriesUseCase,
        project_repo: IProjectRepository,
    ) -> None:
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo
        self._summary_usecase = summary_usecase
        self._list_entries_usecase = list_entries_usecase
        self._project_repo = project_repo

    def execute(self, req: ExportLaborRequest) -> ExportLaborResult:
        """Generate export file.

        Args:
            req: ExportLaborRequest with project_id, from_month, to_month, format,
                 acting_user_email, and optional worker_id for single-worker scope.

        Returns:
            ExportLaborResult with raw bytes, filename, and MIME type.

        Raises:
            ProjectNotFoundError: if project does not exist.
            WorkerNotFoundError: if worker_id is set but worker does not exist or
                belongs to a different project.
        """
        # 1. Resolve project — raises ProjectNotFoundError if absent
        project = self._project_repo.find_by_id(req.project_id)
        if project is None:
            raise ProjectNotFoundError(str(req.project_id))

        # 2. Optionally resolve worker (single-worker scope)
        worker = None
        if req.worker_id is not None:
            worker = self._worker_repo.find_by_id(req.worker_id)
            if worker is None or worker.project_id != req.project_id:
                raise WorkerNotFoundError(str(req.worker_id))

        # 3. Parse month boundaries
        from_d = _parse_yyyy_mm(req.from_month)
        to_d = _parse_yyyy_mm(req.to_month)

        # 4. Build per-month buckets
        buckets: List[MonthBucket] = []
        for month_first in _enumerate_months(from_d, to_d):
            month_start, month_end = _month_bounds(month_first)

            summary = self._summary_usecase.execute(
                GetLaborSummaryRequest(
                    project_id=req.project_id,
                    date_from=month_start,
                    date_to=month_end,
                    worker_id=req.worker_id,
                )
            )

            daily_entries = self._list_entries_usecase.execute(
                ListLaborEntriesRequest(
                    project_id=req.project_id,
                    date_from=month_start,
                    date_to=month_end,
                    worker_id=req.worker_id,
                )
            )

            # Sort daily entries by date then worker_name for deterministic output
            daily_entries.sort(key=lambda e: (e.date, e.worker_name))

            buckets.append(
                MonthBucket(
                    month=month_first,
                    summary=summary,
                    daily_entries=daily_entries,
                )
            )

        # 5. Build export context
        context = ExportContext(
            project_name=project.name,
            project_id=req.project_id,
            range=ExportRange(from_month=from_d, to_month=to_d),
            generated_at=datetime.now(timezone.utc),
            generated_by_email=req.acting_user_email,
            worker_name=worker.name if worker is not None else None,
            worker_daily_rate=worker.daily_rate if worker is not None else None,
        )

        # 6. Dispatch to builder
        from app.domain.labor.export.format import slugify_project_name, slugify_worker_name

        if req.format == "xlsx":
            from app.domain.labor.export.xlsx_builder import build_xlsx

            file_bytes = build_xlsx(context, buckets)
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        else:  # pdf
            from app.domain.labor.export.pdf_builder import build_pdf

            file_bytes = build_pdf(context, buckets)
            mime_type = "application/pdf"
            ext = "pdf"

        # 7. Generate filename
        project_slug = slugify_project_name(project.name, str(project.id))
        if worker is not None:
            worker_slug = slugify_worker_name(worker.name, str(worker.id))
            filename = f"labor-{project_slug}-{worker_slug}-{req.from_month}-to-{req.to_month}.{ext}"
        else:
            filename = f"labor-{project_slug}-{req.from_month}-to-{req.to_month}.{ext}"

        return ExportLaborResult(
            content=file_bytes,
            filename=filename,
            mime_type=mime_type,
        )
