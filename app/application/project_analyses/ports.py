"""Repository and session ports (Protocols) for the project analyses application layer."""

from __future__ import annotations

from typing import Optional, Protocol
from uuid import UUID

from app.domain.entities.project_analysis import ProjectAnalysis

# Re-exported from their canonical shared locations — do NOT redefine these.
# Both modules already carry the minimal contract every write use-case needs.
from app.application.invitations.ports import TransactionalSessionPort as TransactionalSessionPort  # noqa: F401
from app.application.notes.ports import ProjectMembershipReaderPort as ProjectMembershipReaderPort  # noqa: F401


class ProjectAnalysisRepositoryPort(Protocol):
    """Persistence contract for the ProjectAnalysis aggregate."""

    def find_by_id(self, analysis_id: UUID) -> Optional[ProjectAnalysis]:
        """Return the analysis by UUID, or None if no row exists with that id.

        Does NOT filter on deleted_at — callers (use-cases) decide whether a
        soft-deleted analysis should be treated as not-found.
        """
        ...

    def find_by_id_for_update(self, analysis_id: UUID) -> Optional[ProjectAnalysis]:
        """Return the analysis by UUID with a row-level SELECT FOR UPDATE lock.

        Serializes concurrent mutations (e.g. delete racing an update) against
        the same row. Implementations on SQLite (tests) may degrade to a plain
        SELECT — the in-memory test DB has no concurrent transactions.
        """
        ...

    def list_by_project(
        self,
        project_id: UUID,
        *,
        q: Optional[str] = None,
        tags: tuple[str, ...] = (),
        sort: str = "created_at",
        order: str = "desc",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[ProjectAnalysis], int]:
        """Return a paginated, filtered list of active analyses for a project.

        Args:
            project_id: target project UUID.
            q: optional case-insensitive substring match against title + summary.
            tags: optional tag filter — an analysis must carry ALL given tags.
            sort: one of "created_at" | "title".
            order: "asc" | "desc".
            page: 1-based page number.
            per_page: page size.

        Returns:
            (items, total) — items for the requested page, total matching rows
            across all pages (for pagination UI).
        """
        ...

    def list_tags_for_project(self, project_id: UUID) -> list[str]:
        """Return all distinct tags used by active analyses in a project."""
        ...

    def add(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        """Insert a new analysis record and return the persisted entity."""
        ...

    def save(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        """Persist changes to an existing analysis (metadata + tag replace)."""
        ...
