"""ListProductsUseCase — paginated, filtered product list for the company."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.bibliotheque.dtos import LibraryProductResponse
from app.application.bibliotheque.exceptions import CompanyAccessDeniedError
from app.application.bibliotheque.ports import ICompanyMembershipReader, ILibraryProductRepository

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100


class ListProductsUseCase:
    """Return paginated products scoped to the company.

    Filters: supplier_id, category (exact), q (ILIKE on name/description/reference).
    """

    def __init__(
        self,
        product_repo: ILibraryProductRepository,
        membership_reader: ICompanyMembershipReader,
    ) -> None:
        self._product_repo = product_repo
        self._membership = membership_reader

    def execute(
        self,
        *,
        requester_id: UUID,
        company_id: UUID,
        supplier_id: Optional[UUID] = None,
        category: Optional[str] = None,
        q: Optional[str] = None,
        page: int = 1,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> tuple[list[LibraryProductResponse], int]:
        """Return (items, total_count) matching the filters."""
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")

        page = max(1, page)
        page_size = min(max(1, page_size), _MAX_PAGE_SIZE)
        offset = (page - 1) * page_size

        items, total = self._product_repo.list(
            company_id,
            supplier_id=supplier_id,
            category=category,
            q=q,
            limit=page_size,
            offset=offset,
        )
        return [LibraryProductResponse.from_entity(p) for p in items], total
