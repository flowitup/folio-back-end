"""SetInvoiceRefundableStatusUseCase — update refundable_status on a materials_services invoice."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.billing.ports import UserCompanyAccessRepositoryPort, admin_company_ids
from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import IInvoiceRepository
from app.domain.billing.exceptions import ForbiddenCompanyBillingError
from app.domain.entities.invoice import InvoiceType, RefundableStatus
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, InvoiceNotFoundError


class SetInvoiceRefundableStatusUseCase:
    """Set or clear the refundable_status on a materials_services invoice.

    Authorization: the invoice's project must belong to a company where the
    caller holds the admin role (or caller is superadmin). This mirrors the
    company-admin gate used by billing document management.

    Guards:
    - Invoice must exist (404 otherwise)
    - Invoice type must be materials_services (InvalidInvoiceDataError → 400)
    - Non-null value must be a valid RefundableStatus (InvalidInvoiceDataError → 400)
    - Caller must be company-admin for the invoice's company (ForbiddenCompanyBillingError → 403)
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
        invoice_id: UUID,
        refundable_status: Optional[str],
    ) -> InvoiceResponse:
        invoice = self._invoice_repo.find_by_id(invoice_id)
        if invoice is None:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")

        # Type guard: only materials_services invoices support refund tracking
        if invoice.type != InvoiceType.MATERIALS_SERVICES:
            raise InvalidInvoiceDataError(
                f"Refundable status can only be set on materials_services invoices; "
                f"this invoice has type '{invoice.type.value}'"
            )

        # Value guard: non-null value must be a recognised RefundableStatus
        if refundable_status is not None:
            valid_values = {s.value for s in RefundableStatus}
            if refundable_status not in valid_values:
                raise InvalidInvoiceDataError(
                    f"Invalid refundable_status {refundable_status!r}. " f"Allowed: {sorted(valid_values)}"
                )

        # Authorization: resolve the project's company and check admin access
        if not is_superadmin:
            # Fetch project's company_id via the invoice's project_id
            company_id = self._get_project_company_id(invoice.project_id)
            if company_id is None:
                # Project has no company attached — deny non-superadmins.
                # Raise with a sentinel UUID so callers get a typed error; the
                # project_id is not a company_id but no real company_id exists here.
                raise ForbiddenCompanyBillingError(invoice.project_id)

            admin_ids = admin_company_ids(self._access_repo, user_id)
            if company_id not in admin_ids:
                raise ForbiddenCompanyBillingError(company_id)

        updated = invoice.with_updates(refundable_status=refundable_status)
        saved = self._invoice_repo.update(updated)
        return InvoiceResponse.from_entity(saved)

    def _get_project_company_id(self, project_id: UUID) -> Optional[UUID]:
        """Fetch company_id for a project via the invoice repo's session."""
        from app.infrastructure.database.models.project import ProjectModel

        # Access the underlying session through the concrete repo implementation.
        # This avoids a separate ProjectRepository port dependency for a single
        # company_id lookup, keeping the use-case lean.
        session = getattr(self._invoice_repo, "_session", None)
        if session is None:
            return None
        row = session.query(ProjectModel.company_id).filter_by(id=project_id).first()
        return row[0] if row else None
