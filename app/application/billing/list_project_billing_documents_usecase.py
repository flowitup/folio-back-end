"""ListProjectBillingDocumentsUseCase — list billing docs linked to a project.

Access is gated by project:read (owner, member, or admin of the project's company), NOT billing
document ownership. Returns all docs (any kind, any status, any owner)
that have project_id == the requested project_id.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing.dtos import ProjectBillingDocumentSummary
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    ProjectReadPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
)


class ListProjectBillingDocumentsUseCase:
    """Return all billing documents linked to a project.

    Raises ForbiddenProjectAccessError if the caller is not a project member/owner or an admin
    of the project's company.
    Raises ValueError if the project does not exist.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        project_repo: ProjectReadPort,
        access_repo: Optional[UserCompanyAccessRepositoryPort] = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._project_repo = project_repo
        self._access_repo = access_repo

    def execute(
        self,
        project_id: UUID,
        user_id: UUID,
    ) -> list[ProjectBillingDocumentSummary]:
        assert_project_read_access(self._project_repo, project_id, user_id, self._access_repo)
        docs = self._doc_repo.list_by_project(project_id)
        return [ProjectBillingDocumentSummary.from_entity(d) for d in docs]
