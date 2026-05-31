"""ListSuppliersUseCase — return all suppliers for the requester's company."""

from __future__ import annotations

from uuid import UUID

from app.application.bibliotheque.dtos import SupplierResponse
from app.application.bibliotheque.exceptions import CompanyAccessDeniedError
from app.application.bibliotheque.ports import ICompanyMembershipReader, ISupplierRepository


class ListSuppliersUseCase:
    """Return all suppliers scoped to the company.

    Any company member may call this endpoint; no elevated permission required.
    """

    def __init__(
        self,
        supplier_repo: ISupplierRepository,
        membership_reader: ICompanyMembershipReader,
    ) -> None:
        self._supplier_repo = supplier_repo
        self._membership = membership_reader

    def execute(self, *, requester_id: UUID, company_id: UUID) -> list[SupplierResponse]:
        if not self._membership.is_member(requester_id, company_id):
            raise CompanyAccessDeniedError(f"User {requester_id} is not a member of company {company_id}.")
        suppliers = self._supplier_repo.list_by_company(company_id)
        return [SupplierResponse.from_entity(s) for s in suppliers]
