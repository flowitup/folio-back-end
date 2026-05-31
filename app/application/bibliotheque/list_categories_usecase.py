"""ListCategoriesUseCase — return distinct product categories for the company."""

from __future__ import annotations

from uuid import UUID

from app.application.bibliotheque.exceptions import CompanyAccessDeniedError
from app.application.bibliotheque.ports import ICompanyMembershipReader, ILibraryProductRepository


class ListCategoriesUseCase:
    """Return sorted distinct non-null categories for the company's product library."""

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        membership_reader: ICompanyMembershipReader,
    ) -> None:
        self._product_repo = product_repo
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, company_id: UUID) -> list[str]:
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")
        return self._product_repo.distinct_categories(company_id)
