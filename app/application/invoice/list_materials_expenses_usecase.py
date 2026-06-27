"""ListMaterialsExpensesUseCase — company-scoped, cross-project refundable M&S list."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.application.billing.ports import UserCompanyAccessRepositoryPort, admin_company_ids
from app.application.invoice.ports import IInvoiceRepository
from app.domain.billing.exceptions import ForbiddenCompanyBillingError


@dataclass(frozen=True)
class MaterialsExpenseAttachment:
    """Inline attachment metadata embedded in a MaterialsExpenseItem row."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int


@dataclass(frozen=True)
class MaterialsExpenseItem:
    """Single row returned by the listing use-case."""

    id: str
    project_id: str
    project_name: str
    invoice_number: str
    recipient_name: str
    issue_date: str
    total_amount: float
    refundable_status: Optional[str]
    # True when ≥1 refund invoice links back to this expense (refunded by bank).
    # The company-refund signal rides on refundable_status == 'refunded'.
    has_bank_refund: bool
    attachments: list[MaterialsExpenseAttachment]


@dataclass(frozen=True)
class MaterialsExpensesResult:
    """Paginated envelope for materials & services expenses."""

    items: list[MaterialsExpenseItem]
    total: int
    limit: int
    offset: int


class ListMaterialsExpensesUseCase:
    """List materials_services invoices across all accessible company projects.

    Access logic mirrors ListBillingDocumentsUseCase:
    - superadmin sees all companies (or all of a specified company_id)
    - regular user sees only companies where they hold the admin role
    - if company_id is supplied, it is intersected with the accessible set;
      a non-admin requesting another company's data gets 403
    """

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        access_repo: Optional[UserCompanyAccessRepositoryPort] = None,
    ) -> None:
        self._invoice_repo = invoice_repo
        self._access_repo = access_repo

    def execute(
        self,
        user_id: UUID,
        is_superadmin: bool,
        company_id: Optional[UUID] = None,
        refundable: Optional[bool] = True,
        limit: int = 50,
        offset: int = 0,
    ) -> MaterialsExpensesResult:
        if is_superadmin:
            # Superadmin: pass company_id as single-element list or None to signal "all"
            effective_ids: Optional[list[UUID]] = [company_id] if company_id else None
        else:
            accessible = admin_company_ids(self._access_repo, user_id)
            if company_id is not None:
                if company_id not in accessible:
                    raise ForbiddenCompanyBillingError(company_id)
                effective_ids = [company_id]
            else:
                effective_ids = accessible

        if not is_superadmin and not effective_ids:
            # Non-admin with no admin companies → empty result, not an error
            return MaterialsExpensesResult(items=[], total=0, limit=limit, offset=offset)

        rows, total = self._invoice_repo.list_materials_services_by_companies(
            company_ids=effective_ids or [],
            refundable=refundable,
            limit=limit,
            offset=offset,
            all_companies=is_superadmin and company_id is None,
        )

        # Batch reverse-lookup: which of this page's expenses have a linked refund
        # invoice (refunded by bank). One query for the whole page — no N+1.
        bank_refunded = self._invoice_repo.refund_source_ids([UUID(r["id"]) for r in rows])

        items = [
            MaterialsExpenseItem(
                id=r["id"],
                project_id=r["project_id"],
                project_name=r["project_name"],
                invoice_number=r["invoice_number"],
                recipient_name=r["recipient_name"],
                issue_date=r["issue_date"],
                total_amount=r["total_amount"],
                refundable_status=r["refundable_status"],
                has_bank_refund=UUID(r["id"]) in bank_refunded,
                attachments=[
                    MaterialsExpenseAttachment(
                        id=a["id"],
                        filename=a["filename"],
                        mime_type=a["mime_type"],
                        size_bytes=a["size_bytes"],
                    )
                    for a in r.get("attachments", [])
                ],
            )
            for r in rows
        ]
        return MaterialsExpensesResult(items=items, total=total, limit=limit, offset=offset)
