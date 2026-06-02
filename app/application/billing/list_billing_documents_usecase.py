"""ListBillingDocumentsUseCase — paginated list of billing documents for a user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.billing.dtos import BillingDocumentResponse
from app.application.billing.ports import (
    BillingDocumentRepositoryPort,
    ProjectReadPort,
    UserCompanyAccessRepositoryPort,
    admin_company_ids,
    assert_project_read_access,
)
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus


@dataclass(frozen=True)
class ListBillingDocumentsResult:
    """Paginated result envelope."""

    items: list[BillingDocumentResponse]
    total: int
    limit: int
    offset: int


class ListBillingDocumentsUseCase:
    """Return a paginated, filtered list of billing documents visible to the caller.

    Visibility: documents the caller owns (user_id == row.user_id) PLUS documents
    belonging to any company where the caller holds the 'admin' role. Superadmins
    (``is_superadmin=True``) see all documents. Company 'member's see only their
    own documents — this is what gates the billing section per-company.
    """

    def __init__(
        self,
        doc_repo: BillingDocumentRepositoryPort,
        project_repo: ProjectReadPort = None,  # type: ignore[assignment]
        access_repo: UserCompanyAccessRepositoryPort = None,  # type: ignore[assignment]
    ) -> None:
        self._doc_repo = doc_repo
        self._project_repo = project_repo
        self._access_repo = access_repo

    def execute(
        self,
        user_id: UUID,
        kind: BillingDocumentKind,
        status: Optional[BillingDocumentStatus] = None,
        project_id: Optional[UUID] = None,
        company_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
        is_superadmin: bool = False,
    ) -> ListBillingDocumentsResult:
        # H1: Verify project:read access before filtering by project_id
        assert_project_read_access(self._project_repo, project_id, user_id)

        docs, total = self._doc_repo.list_visible(
            kind,
            owner_id=user_id,
            company_ids=admin_company_ids(self._access_repo, user_id),
            all_documents=is_superadmin,
            status=status,
            project_id=project_id,
            company_id=company_id,
            limit=limit,
            offset=offset,
        )
        return ListBillingDocumentsResult(
            items=[BillingDocumentResponse.from_entity(d) for d in docs],
            total=total,
            limit=limit,
            offset=offset,
        )
