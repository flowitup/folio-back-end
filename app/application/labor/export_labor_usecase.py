"""ExportLaborUseCase — stub; full implementation in phase-04.

Orchestrates multi-month summary + daily entries → xlsx/pdf bytes via
domain builders (xlsx_builder.py / pdf_builder.py, phase-02/03).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.labor.export.models import ExportFormat


@dataclass
class ExportLaborRequest:
    """Input DTO for the export use case."""

    project_id: str
    from_month: str  # YYYY-MM  e.g. "2026-01"
    to_month: str  # YYYY-MM  e.g. "2026-03"
    format: ExportFormat  # "xlsx" | "pdf"
    acting_user_email: str


@dataclass
class ExportLaborResult:
    """Output DTO returned to the API layer."""

    content: bytes
    filename: str  # e.g. "labor-downtown-office-tower-2026-01-to-2026-03.xlsx"
    mime_type: str  # e.g. "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ExportLaborUseCase:
    """Orchestrate multi-month labor data into a downloadable file.

    Constructor dependencies (injected by the DI container in phase-04):
      worker_repo       — IWorkerRepository
      entry_repo        — ILaborEntryRepository
      summary_usecase   — GetLaborSummaryUseCase
      project_repo      — IProjectRepository
      db_session        — SQLAlchemy Session (for any direct queries)

    Method:
      execute(req: ExportLaborRequest) -> ExportLaborResult
        Returns (file_bytes, filename, mime_type) packed in ExportLaborResult.
    """

    def __init__(
        self,
        worker_repo: object,
        entry_repo: object,
        summary_usecase: object,
        project_repo: object,
        db_session: object,
    ) -> None:
        self._worker_repo = worker_repo
        self._entry_repo = entry_repo
        self._summary_usecase = summary_usecase
        self._project_repo = project_repo
        self._db_session = db_session

    def execute(self, req: ExportLaborRequest) -> ExportLaborResult:
        """Generate export file.

        Returns:
            ExportLaborResult with raw bytes, filename, and MIME type.

        Raises:
            NotImplementedError: until phase-04 provides the implementation.
        """
        raise NotImplementedError("ExportLaborUseCase.execute implemented in phase-04")
