"""Use case: fetch the raw HTML body of an analysis report from object storage."""

from __future__ import annotations

from uuid import UUID

from app.application.invoice.ports import IAttachmentStorage
from app.application.project_analyses.dtos import AnalysisOutput
from app.application.project_analyses.exceptions import AnalysisNotFoundError, NotProjectMemberError
from app.application.project_analyses.ports import ProjectAnalysisRepositoryPort, ProjectMembershipReaderPort


class GetProjectAnalysisContentUseCase:
    """Look up an analysis and open a read stream for its stored HTML body.

    Authorization: the acting user must be a member of the project. The
    membership check happens BEFORE opening the storage stream so a member of
    another project guessing an analysis UUID cannot exhaust backend→storage
    connections via repeated probing (mirrors GetProjectDocumentUseCase).
    """

    def __init__(
        self,
        repo: ProjectAnalysisRepositoryPort,
        storage: IAttachmentStorage,
        membership_reader: ProjectMembershipReaderPort,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._membership = membership_reader

    def execute(
        self, *, actor_id: UUID, analysis_id: UUID, expected_project_id: UUID
    ) -> tuple[AnalysisOutput, bytes, int]:
        """Return (metadata DTO, raw HTML bytes, byte length).

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

        stream, length = self._storage.get_stream(analysis.storage_key)
        body = stream.read()
        return AnalysisOutput.from_entity(analysis), body, length or len(body)
