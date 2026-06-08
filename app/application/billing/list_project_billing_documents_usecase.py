"""ListProjectBillingDocumentsUseCase — list billing docs linked to a project.

Access is gated by project:read membership (owner OR member), NOT billing
document ownership. Returns all docs (any kind, any status, any owner)
that have project_id == the requested project_id.
"""

from __future__ import annotations

from uuid import UUID

from app.application.billing.dtos import ProjectBillingDocumentSummary
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    ProjectReadPort,
    assert_project_read_access,
)


class ListProjectBillingDocumentsUseCase:
    """Return all billing documents linked to a project.

    Raises ForbiddenProjectAccessError if the caller is not a project member/owner.
    Raises ValueError if the project does not exist.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        project_repo: ProjectReadPort,
    ) -> None:
        self._doc_repo = doc_repo
        self._project_repo = project_repo

    def execute(
        self,
        project_id: UUID,
        user_id: UUID,
    ) -> list[ProjectBillingDocumentSummary]:
        assert_project_read_access(self._project_repo, project_id, user_id)
        docs = self._doc_repo.list_by_project(project_id)
        return [ProjectBillingDocumentSummary.from_entity(d) for d in docs]
