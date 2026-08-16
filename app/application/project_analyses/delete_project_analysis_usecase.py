"""Use case: soft-delete a project analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.application.project_analyses.exceptions import AnalysisNotFoundError, NotProjectMemberError
from app.application.project_analyses.ports import (
    ProjectAnalysisRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class DeleteProjectAnalysisUseCase:
    """Soft-delete an analysis by id.

    Authorization: the acting user must be a member of the analysis's project.
    The underlying storage object is NOT removed — soft-delete preserves it
    for audit trails, mirroring DeleteProjectDocumentUseCase.
    """

    def __init__(
        self,
        repo: ProjectAnalysisRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
        db_session: TransactionalSessionPort,
    ) -> None:
        self._repo = repo
        self._membership = membership_reader
        self._db = db_session

    def execute(self, *, actor_id: UUID, analysis_id: UUID, expected_project_id: UUID) -> None:
        """Soft-delete the analysis; raise if not found or actor lacks membership.

        Raises:
            AnalysisNotFoundError: analysis does not exist, is already
                soft-deleted, or belongs to a different project.
            NotProjectMemberError: actor is not a member of the analysis's project.
        """
        analysis = self._repo.find_by_id_for_update(analysis_id)
        if analysis is None or analysis.deleted_at is not None:
            raise AnalysisNotFoundError(f"Analysis {analysis_id} not found")
        if analysis.project_id != expected_project_id:
            raise AnalysisNotFoundError(f"Analysis {analysis_id} not found")

        if not self._membership.is_member(actor_id, analysis.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {analysis.project_id}.")

        deleted = analysis.soft_delete(datetime.now(timezone.utc))
        self._repo.save(deleted)
        self._db.commit()
