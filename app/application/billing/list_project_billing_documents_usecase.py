"""ListProjectBillingDocumentsUseCase — list billing docs linked to a project.

Access to the list is gated by project:read (owner, member, or admin of the project's
company). Billing data itself stays with the people who may open each document: a
company admin (or platform ops) sees every document linked to the project, anyone
else only the documents they own — the same rule as opening one document.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing._helpers import _assert_billing_doc_access
from app.application.billing.dtos import ProjectBillingDocumentSummary
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    ProjectReadPort,
    UserCompanyAccessRepositoryPort,
    assert_project_read_access,
)
from app.domain.billing.exceptions import ForbiddenBillingDocumentError


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
        is_platform_ops: bool = False,
    ) -> list[ProjectBillingDocumentSummary]:
        assert_project_read_access(self._project_repo, project_id, user_id, self._access_repo)
        docs = self._doc_repo.list_by_project(project_id)
        if not is_platform_ops:
            docs = [d for d in docs if self._can_open(d, user_id)]
        return [ProjectBillingDocumentSummary.from_entity(d) for d in docs]

    def _can_open(self, doc, user_id: UUID) -> bool:
        try:
            _assert_billing_doc_access(doc, user_id, self._access_repo)
        except ForbiddenBillingDocumentError:
            return False
        return True
