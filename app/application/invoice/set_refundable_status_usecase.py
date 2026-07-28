"""SetInvoiceRefundableStatusUseCase — update refundable_status on a materials_services invoice."""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from app.application.billing.ports import UserCompanyAccessRepositoryPort, admin_company_ids
from app.application.invoice.dtos import InvoiceResponse
from app.application.invoice.ports import BankRefundReleasePort, IInvoiceRepository
from app.domain.billing.exceptions import ForbiddenCompanyBillingError
from app.domain.entities.invoice import InvoiceType, RefundableStatus, RefundedBy
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, InvoiceNotFoundError

log = logging.getLogger(__name__)


class SetInvoiceRefundableStatusUseCase:
    """Set or clear the refundable_status on a materials_services invoice.

    Authorization: the invoice's project must belong to a company where the
    caller holds the admin role (or caller is superadmin). This mirrors the
    company-admin gate used by billing document management.

    Guards (in order):
    - Invoice must exist (404 otherwise)
    - Caller must be company-admin for the invoice's company (ForbiddenCompanyBillingError → 403)
    - Invoice type must be materials_services (InvalidInvoiceDataError → 400)
    - Non-null value must be a valid RefundableStatus (InvalidInvoiceDataError → 400)
    - Invoice must not be paid via a company-direct method (InvalidInvoiceDataError → 400)

    refunded_by ('company' | 'bank') is only meaningful when refundable_status ==
    'refunded': setting any other status (including null) forces refunded_by to NULL,
    silently ignoring any provided value. Setting 'refunded' with refunded_by omitted
    or null defaults to 'company' (legacy-compatible). An invalid refunded_by value
    while transitioning to 'refunded' raises InvalidInvoiceDataError.

    Bank-refund reconciliation: when funds_release is provided, every resulting
    state is reconciled against its linked released_funds invoice — created
    when the result is 'refunded' with refunded_by in ('bank', 'both'), deleted
    otherwise (idempotent either way, so repeating the same call is a no-op).

    Reconciliation runs BEFORE the repo persists the status change. This is
    NOT one atomic transaction with that persist: IInvoiceRepository.create(),
    .update(), and .delete_bank_refund_release() all commit internally (this
    use-case takes no session to wrap them), so reconcile-then-update is two
    separate transactions no matter which runs first. The ordering is still
    deliberate:
    - If reconciliation itself raises, no status change has been attempted
      yet, so there is nothing to undo — refundable_status/refunded_by stay
      at their prior persisted value, and the next successful call
      re-converges both the release and the status together.
    - If the later repo.update() call raises, the release was already
      committed by the reconcile step above; execute() then runs a
      best-effort compensating delete for a release it just created (see the
      try/except around repo.update() for exactly what it covers and its
      residual limits — e.g. it cannot undo a release that was just deleted,
      only one that was just created).

    funds_release=None skips reconciliation entirely (existing direct
    instantiations keep working unchanged) but logs a WARNING the first time,
    so a mis-wire in production is observable rather than silently returning
    200 with no release created.
    """

    def __init__(
        self,
        invoice_repo: IInvoiceRepository,
        access_repo: Optional[UserCompanyAccessRepositoryPort] = None,
        funds_release: Optional[BankRefundReleasePort] = None,
    ) -> None:
        self._invoice_repo = invoice_repo
        self._access_repo = access_repo
        self._funds_release = funds_release
        self._warned_missing_funds_release = False

    def execute(
        self,
        user_id: UUID,
        is_superadmin: bool,
        invoice_id: UUID,
        refundable_status: Optional[str],
        refunded_by: Optional[str] = None,
    ) -> InvoiceResponse:
        invoice = self._invoice_repo.find_by_id(invoice_id)
        if invoice is None:
            raise InvoiceNotFoundError(f"Invoice {invoice_id} not found")

        # Authorization first: callers without company-admin rights receive 403
        # regardless of invoice state, so no business detail leaks to non-admins.
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

        # Company-payment guard: if the invoice was paid directly by the company,
        # refund tracking does not apply — the expense is already attributed to
        # the company via the payment method flag.  Clearing (null) stays allowed.
        if refundable_status is not None and invoice.payment_method_id is not None:
            if self._is_company_payment_method(invoice.payment_method_id, invoice.project_id):
                raise InvalidInvoiceDataError("Expense already paid by the company — refund tracking does not apply")

        # refunded_by is only meaningful when transitioning to 'refunded'. Any other
        # status (including null) forces it to NULL, silently dropping whatever the
        # caller sent — it is not a valid state outside of 'refunded'.
        if refundable_status == RefundableStatus.REFUNDED.value:
            if refunded_by is None:
                effective_refunded_by: Optional[str] = RefundedBy.COMPANY.value
            else:
                valid_refunded_by = {b.value for b in RefundedBy}
                if refunded_by not in valid_refunded_by:
                    raise InvalidInvoiceDataError(
                        f"Invalid refunded_by {refunded_by!r}. Allowed: {sorted(valid_refunded_by)}"
                    )
                effective_refunded_by = refunded_by
        else:
            effective_refunded_by = None

        updated = invoice.with_updates(refundable_status=refundable_status, refunded_by=effective_refunded_by)

        # Reconcile the linked bank-refund release before persisting the status
        # change (see class docstring for why this must run first). None of the
        # fields the release depends on (id, project_id, issue_date,
        # recipient_name, total_amount) are touched by this update, so
        # reconciling against the pre-persist `updated` entity is equivalent to
        # reconciling against the post-persist one.
        # reconcile_action tracks what just happened so a subsequent repo.update()
        # failure can be compensated correctly (see below).
        reconcile_action: Optional[str] = None  # None | "created" | "deleted"
        if self._funds_release is not None:
            should_have_release = refundable_status == RefundableStatus.REFUNDED.value and effective_refunded_by in (
                RefundedBy.BANK.value,
                RefundedBy.BOTH.value,
            )
            if should_have_release:
                self._funds_release.create_bank_refund_release(updated, created_by=user_id)
                reconcile_action = "created"
            else:
                self._funds_release.delete_bank_refund_release(updated.id)
                reconcile_action = "deleted"
        elif not self._warned_missing_funds_release:
            log.warning(
                "SetInvoiceRefundableStatusUseCase has no funds_release wired — skipping "
                "bank-refund release reconciliation for invoice %s. If this is production "
                "traffic, the use-case was constructed without its FundsReleaseAdapter "
                "(a wiring bug), not an intentional no-op.",
                updated.id,
            )
            self._warned_missing_funds_release = True

        try:
            saved = self._invoice_repo.update(updated)
        except Exception:
            # repo.update() commits internally (see class docstring), so this
            # failure happens strictly AFTER the reconcile step above already
            # committed — the two are separate transactions, not one. This is
            # best-effort compensation, not a rollback:
            #   - reconcile_action == "created": the release above is now
            #     committed but the status change that was supposed to
            #     accompany it never landed. Delete it so it doesn't survive
            #     as a phantom (it would otherwise permanently inflate
            #     sum_funds_released and, being auto-generated, cannot be
            #     removed later via the API).
            #   - reconcile_action == "deleted": nothing to compensate —
            #     recreating would mint a different FR number than the one
            #     just removed, so we only log for investigation; the invoice
            #     itself still carries its prior (unpersisted-change)
            #     refundable_status/refunded_by, which no longer matches the
            #     now-missing release until a later call re-converges them.
            # Either way, compensation failures are swallowed (logged, not
            # raised) and the ORIGINAL exception always propagates — callers
            # must see the real failure, not one from this cleanup attempt.
            if reconcile_action == "created":
                try:
                    self._funds_release.delete_bank_refund_release(updated.id)
                except Exception:
                    log.error(
                        "Compensating delete of bank-refund release for invoice %s failed "
                        "after repo.update() raised — the release may now be orphaned",
                        updated.id,
                        exc_info=True,
                    )
            elif reconcile_action == "deleted":
                log.error(
                    "repo.update() raised for invoice %s after its bank-refund release was "
                    "already deleted; refundable_status/refunded_by were not persisted, so "
                    "they are now inconsistent with the missing release — no automatic "
                    "compensation is possible",
                    updated.id,
                    exc_info=True,
                )
            raise

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

    def _is_company_payment_method(self, payment_method_id: UUID, project_id: UUID) -> bool:
        """Return True if payment_method_id is flagged is_company_payment for the project's company.

        Scoped to the invoice project's company so a method from a different company
        cannot accidentally match.  When the repo has no real session (test fakes that
        omit _session), returns False — authorization still applies independently.
        """
        from app.infrastructure.database.models.payment_method import PaymentMethodModel

        session = getattr(self._invoice_repo, "_session", None)
        if session is None:
            # Test fakes without a real session skip this guard; authz is enforced separately.
            return False
        company_id = self._get_project_company_id(project_id)
        if company_id is None:
            return False
        row = (
            session.query(PaymentMethodModel.is_company_payment)
            .filter(
                PaymentMethodModel.id == payment_method_id,
                PaymentMethodModel.company_id == company_id,
                PaymentMethodModel.is_company_payment.is_(True),
            )
            .first()
        )
        return row is not None
