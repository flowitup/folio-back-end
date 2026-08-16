"""Use case: edit an existing project analysis's metadata."""

from __future__ import annotations

from uuid import UUID

from app.application.project_analyses.dtos import AnalysisOutput, UpdateAnalysisInput
from app.application.project_analyses.exceptions import AnalysisNotFoundError, NotProjectMemberError
from app.application.project_analyses.ports import (
    ProjectAnalysisRepositoryPort,
    ProjectMembershipReaderPort,
    TransactionalSessionPort,
)


class UpdateProjectAnalysisUseCase:
    """Update title, summary, source_url, or tags on an analysis.

    Authorization: the acting user must be a member of the analysis's project.

    CRITICAL: every optional field on ``UpdateAnalysisInput`` (summary,
    source_url, tags) is threaded straight through to
    ``ProjectAnalysis.with_updates(...)`` unchanged — a PATCH that only
    specifies ``summary`` must NOT null out title/source_url/tags. The
    ``_UNSET`` sentinel on the DTO defaults (see dtos.py) is what makes that
    safe: the route only sets fields the client actually sent.
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

    def execute(self, *, actor_id: UUID, expected_project_id: UUID, data: UpdateAnalysisInput) -> AnalysisOutput:
        """Apply updates and return the updated AnalysisOutput.

        Raises:
            AnalysisNotFoundError: analysis does not exist, is soft-deleted,
                or belongs to a different project.
            NotProjectMemberError: actor is not a member of the analysis's project.
            ValueError: title, summary, source_url, or tags fail validation.
        """
        analysis = self._repo.find_by_id_for_update(data.analysis_id)
        if analysis is None or analysis.deleted_at is not None:
            raise AnalysisNotFoundError(f"Analysis {data.analysis_id} not found")
        if analysis.project_id != expected_project_id:
            raise AnalysisNotFoundError(f"Analysis {data.analysis_id} not found")

        if not self._membership.is_member(actor_id, analysis.project_id):
            raise NotProjectMemberError(f"User {actor_id} is not a member of project {analysis.project_id}.")

        updated = analysis.with_updates(
            title=data.title,
            summary=data.summary,
            source_url=data.source_url,
            tags=data.tags,
        )

        saved = self._repo.save(updated)
        self._db.commit()
        return AnalysisOutput.from_entity(saved)
