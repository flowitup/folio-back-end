"""Unit tests for SetInvoiceRefundableStatusUseCase.

Focuses on:
- Non-superadmin (plain company-admin) resolution path via admin_company_ids()
- Fail-closed behaviour when invoice's project has no resolvable company
- Superadmin bypass
- Type guard (non-materials_services invoice → 400)
- IDOR guard: non-admin accessing other company's invoice → 403
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.application.billing.ports import UserCompanyAccessRepositoryPort
from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.set_refundable_status_usecase import SetInvoiceRefundableStatusUseCase
from app.domain.billing.exceptions import ForbiddenCompanyBillingError
from app.domain.companies.user_company_access import UserCompanyAccess
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, InvoiceNotFoundError


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_invoice(
    invoice_id: Optional[UUID] = None,
    project_id: Optional[UUID] = None,
    invoice_type: InvoiceType = InvoiceType.MATERIALS_SERVICES,
    refundable_status: Optional[str] = None,
    payment_method_id: Optional[UUID] = None,
) -> Invoice:
    now = datetime.now(timezone.utc)
    return Invoice(
        id=invoice_id or uuid4(),
        project_id=project_id or uuid4(),
        invoice_number="INV-TEST-0001",
        type=invoice_type,
        issue_date=date.today(),
        recipient_name="Test Supplier",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
        refundable_status=refundable_status,
        payment_method_id=payment_method_id,
    )


def _make_access_repo(user_id: UUID, company_ids_with_admin: list[UUID]) -> UserCompanyAccessRepositoryPort:
    """Return a minimal mock access_repo: list_for_user returns admin rows for given companies."""
    now = datetime.now(timezone.utc)
    access_repo = MagicMock(spec=UserCompanyAccessRepositoryPort)
    accesses = [
        UserCompanyAccess(
            user_id=user_id,
            company_id=cid,
            is_primary=True,
            attached_at=now,
            role="admin",
        )
        for cid in company_ids_with_admin
    ]
    access_repo.list_for_user.return_value = accesses
    return access_repo


def _make_invoice_repo(
    invoice: Optional[Invoice] = None, company_id_for_project: Optional[UUID] = None
) -> IInvoiceRepository:
    """Return a mock invoice_repo pre-loaded with *invoice*.

    The _session attribute is set so _get_project_company_id can be exercised
    through a patched query, or we monkeypatch _get_project_company_id directly.
    """
    repo = MagicMock(spec=IInvoiceRepository)
    repo.find_by_id.return_value = invoice
    repo.update.side_effect = lambda inv: inv  # pass-through
    # No real session — callers must patch _get_project_company_id instead
    repo._session = None
    return repo


# ---------------------------------------------------------------------------
# Non-superadmin: company-admin resolution path
# ---------------------------------------------------------------------------


class TestNonSuperadminCompanyAdminPath:
    def test_plain_admin_of_correct_company_can_set_status(self, monkeypatch):
        """Plain company-admin who admins the invoice's company → 200."""
        user_id = uuid4()
        company_id = uuid4()
        project_id = uuid4()
        inv = _make_invoice(project_id=project_id)

        invoice_repo = _make_invoice_repo(invoice=inv)
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[company_id])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        # Patch the private helper to return the known company_id
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: company_id)

        result = uc.execute(
            user_id=user_id,
            is_superadmin=False,
            invoice_id=inv.id,
            refundable_status="refundable",
        )

        assert result.refundable_status == "refundable"

    def test_plain_admin_of_different_company_raises_forbidden(self, monkeypatch):
        """Plain admin of company-A trying to patch company-B invoice → ForbiddenCompanyBillingError."""
        user_id = uuid4()
        company_a = uuid4()
        company_b = uuid4()
        project_id = uuid4()
        inv = _make_invoice(project_id=project_id)

        invoice_repo = _make_invoice_repo(invoice=inv)
        # user is admin of company_a only
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[company_a])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        # Invoice belongs to company_b
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: company_b)

        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(
                user_id=user_id,
                is_superadmin=False,
                invoice_id=inv.id,
                refundable_status="refundable",
            )

    def test_fail_closed_when_project_has_no_company(self, monkeypatch):
        """When invoice's project has no resolvable company, non-superadmin is DENIED, not allowed."""
        user_id = uuid4()
        project_id = uuid4()
        inv = _make_invoice(project_id=project_id)

        invoice_repo = _make_invoice_repo(invoice=inv)
        # User has admin on some company but project has no company
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[uuid4()])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        # Project returns None for company_id — no company attached
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: None)

        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(
                user_id=user_id,
                is_superadmin=False,
                invoice_id=inv.id,
                refundable_status="refundable",
            )

    def test_fail_closed_when_access_repo_returns_empty(self, monkeypatch):
        """Non-admin user with no admin companies at all is denied even if project has a company."""
        user_id = uuid4()
        company_id = uuid4()
        project_id = uuid4()
        inv = _make_invoice(project_id=project_id)

        invoice_repo = _make_invoice_repo(invoice=inv)
        # User is admin of zero companies
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: company_id)

        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(
                user_id=user_id,
                is_superadmin=False,
                invoice_id=inv.id,
                refundable_status="refundable",
            )


# ---------------------------------------------------------------------------
# Superadmin bypass
# ---------------------------------------------------------------------------


class TestSuperadminBypass:
    def test_superadmin_can_set_status_without_company_check(self, monkeypatch):
        """Superadmin skips the company-admin resolution path entirely."""
        user_id = uuid4()
        inv = _make_invoice()

        invoice_repo = _make_invoice_repo(invoice=inv)
        access_repo = MagicMock(spec=UserCompanyAccessRepositoryPort)

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)

        result = uc.execute(
            user_id=user_id,
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
        )

        assert result.refundable_status == "refunded"
        # access_repo must NOT be consulted at all for superadmins
        access_repo.list_for_user.assert_not_called()


# ---------------------------------------------------------------------------
# Guard: invoice not found
# ---------------------------------------------------------------------------


class TestInvoiceNotFound:
    def test_missing_invoice_raises_not_found(self):
        invoice_repo = _make_invoice_repo(invoice=None)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        with pytest.raises(InvoiceNotFoundError):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=uuid4(),
                refundable_status="refundable",
            )


# ---------------------------------------------------------------------------
# Guard: wrong invoice type
# ---------------------------------------------------------------------------


class TestWrongInvoiceType:
    def test_labor_invoice_raises_invalid_data(self):
        inv = _make_invoice(invoice_type=InvoiceType.LABOR)
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        with pytest.raises(InvalidInvoiceDataError):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refundable",
            )


# ---------------------------------------------------------------------------
# refunded_by semantics
# ---------------------------------------------------------------------------


class TestRefundedBySemantics:
    def test_refunded_defaults_to_company_when_omitted(self):
        """refundable_status='refunded' with refunded_by omitted → defaults to 'company'."""
        inv = _make_invoice()
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
        )

        assert result.refundable_status == "refunded"
        assert result.refunded_by == "company"

    def test_refunded_by_bank_is_persisted(self):
        """refundable_status='refunded' with refunded_by='bank' is honored as-is."""
        inv = _make_invoice()
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by="bank",
        )

        assert result.refundable_status == "refunded"
        assert result.refunded_by == "bank"

    def test_refunded_by_both_is_persisted(self):
        """'both' is a valid source: partial reimbursement from each side."""
        inv = _make_invoice()
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by="both",
        )

        assert result.refundable_status == "refunded"
        assert result.refunded_by == "both"

    def test_refunded_by_cleared_on_non_refunded_status(self):
        """Moving to 'refundable' silently drops any provided refunded_by (forced to None)."""
        inv = _make_invoice(refundable_status="refunded")
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refundable",
            refunded_by="bank",
        )

        assert result.refundable_status == "refundable"
        assert result.refunded_by is None

    def test_refunded_by_cleared_on_null_status(self):
        """Clearing refundable_status (null) also forces refunded_by to None."""
        inv = _make_invoice(refundable_status="refunded")
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status=None,
            refunded_by="company",
        )

        assert result.refundable_status is None
        assert result.refunded_by is None

    def test_invalid_refunded_by_raises(self):
        """An unrecognised refunded_by value while transitioning to 'refunded' raises 400."""
        inv = _make_invoice()
        invoice_repo = _make_invoice_repo(invoice=inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)

        with pytest.raises(InvalidInvoiceDataError):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="cash",
            )


# ---------------------------------------------------------------------------
# Guard order: authorization must fire before business guards
# ---------------------------------------------------------------------------


class TestAuthorizationBeforeBusinessGuards:
    """Non-admins must receive 403 even when invoice state would produce a 400.

    This ensures no business detail (type, company-payment status) leaks to
    callers who lack company-admin rights.
    """

    def test_non_admin_gets_403_for_labor_invoice(self, monkeypatch):
        """Non-admin calling with a labor invoice gets 403, not 400 (type guard should not fire first)."""
        user_id = uuid4()
        company_id = uuid4()
        inv = _make_invoice(invoice_type=InvoiceType.LABOR)

        invoice_repo = _make_invoice_repo(invoice=inv)
        # User has NO admin access to any company
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: company_id)

        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(
                user_id=user_id,
                is_superadmin=False,
                invoice_id=inv.id,
                refundable_status="refundable",
            )

    def test_non_admin_gets_403_for_company_paid_invoice(self, monkeypatch):
        """Non-admin calling with a company-paid invoice gets 403, not 400 (company-payment guard must not fire first)."""
        user_id = uuid4()
        company_id = uuid4()
        # Give the invoice a payment_method_id so the company-payment guard would fire if reached
        inv = _make_invoice(invoice_type=InvoiceType.MATERIALS_SERVICES, payment_method_id=uuid4())

        invoice_repo = _make_invoice_repo(invoice=inv)
        access_repo = _make_access_repo(user_id=user_id, company_ids_with_admin=[])

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, access_repo=access_repo)
        monkeypatch.setattr(uc, "_get_project_company_id", lambda pid: company_id)
        # Even if the method would be flagged, authz must fire first
        monkeypatch.setattr(uc, "_is_company_payment_method", lambda pm_id, proj_id: True)

        with pytest.raises(ForbiddenCompanyBillingError):
            uc.execute(
                user_id=user_id,
                is_superadmin=False,
                invoice_id=inv.id,
                refundable_status="refundable",
            )
