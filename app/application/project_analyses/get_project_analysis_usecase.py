"""Use case: fetch a single analysis's metadata."""

from __future__ import annotations

from uuid import UUID

from app.application.project_analyses.dtos import AnalysisOutput
from app.application.project_analyses.exceptions import AnalysisNotFoundError, NotProjectMemberError
from app.application.project_analyses.ports import ProjectAnalysisRepositoryPort, ProjectMembershipReaderPort


class GetProjectAnalysisUseCase:
    """Look up a single analysis's metadata by id.

    Authorization: the acting user must be a member of the project.
    """

    def __init__(
        self,
        repo: ProjectAnalysisRepositoryPort,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._repo = repo
        self._membership = membership_reader

    def execute(self, *, actor_id: UUID, analysis_id: UUID, expected_project_id: UUID) -> AnalysisOutput:
        """Return the analysis metadata as a DTO.

        Args:
            actor_id: UUID of the authenticated requester.
            analysis_id: UUID of the analysis to fetch.
            expected_project_id: Project UUID from the URL; must match the
                analysis's project_id. A mismatch is treated identically to
                "not found" so existence is never leaked across projects.

        Raises:
            AnalysisNotFoundError: analysis does not exist, is soft-deleted,
                or belongs to a different project.
            NotProjectMemberError: actor is not a member of the project.
        """
        if not self._membership.is_member(actor_id, expected_project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {expected_project_id}.")

        analysis = self._repo.find_by_id(analysis_id)
        if analysis is None or analysis.deleted_at is not None:
            raise AnalysisNotFoundError(f"Analysis {analysis_id} not found")
        if analysis.project_id != expected_project_id:
            raise AnalysisNotFoundError(f"Analysis {analysis_id} not found")

        return AnalysisOutput.from_entity(analysis)
