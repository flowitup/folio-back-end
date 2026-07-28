"""Unit tests for the bank-refund funds-release reconciliation wired into
SetInvoiceRefundableStatusUseCase (phase 03).

Focuses on:
- create_bank_refund_release is called when the result is refunded+bank/both
- delete_bank_refund_release is called for every other resulting state
- funds_release=None skips reconciliation entirely (backward compatible)
- reconciliation runs BEFORE the repo persists the status change, so a
  reconcile failure never leaves a persisted status change behind
- created_by is the requester's user_id
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invoice.ports import BankRefundReleasePort, IInvoiceRepository
from app.application.invoice.set_refundable_status_usecase import SetInvoiceRefundableStatusUseCase
from app.domain.entities.invoice import Invoice, InvoiceType


def _make_invoice(refundable_status: Optional[str] = None, refunded_by: Optional[str] = None) -> Invoice:
    now = datetime.now(timezone.utc)
    return Invoice(
        id=uuid4(),
        project_id=uuid4(),
        invoice_number="INV-TEST-0001",
        type=InvoiceType.MATERIALS_SERVICES,
        issue_date=date.today(),
        recipient_name="Test Supplier",
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
        refundable_status=refundable_status,
        refunded_by=refunded_by,
    )


def _make_invoice_repo(invoice: Invoice) -> IInvoiceRepository:
    repo = MagicMock(spec=IInvoiceRepository)
    repo.find_by_id.return_value = invoice
    repo.update.side_effect = lambda inv: inv
    repo._session = None
    return repo


def _make_funds_release() -> BankRefundReleasePort:
    return MagicMock(spec=BankRefundReleasePort)


class TestReconcileCreatesOnBankOrBoth:
    @pytest.mark.parametrize("refunded_by", ["bank", "both"])
    def test_create_called_when_result_is_refunded_bank_or_both(self, refunded_by):
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()
        user_id = uuid4()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)
        result = uc.execute(
            user_id=user_id,
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by=refunded_by,
        )

        assert result.refunded_by == refunded_by
        funds_release.create_bank_refund_release.assert_called_once()
        called_source, kwargs = funds_release.create_bank_refund_release.call_args
        assert called_source[0].id == inv.id
        assert kwargs["created_by"] == user_id
        funds_release.delete_bank_refund_release.assert_not_called()


class TestReconcileDeletesOnEveryOtherState:
    def test_delete_called_when_refunded_by_company(self):
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)
        uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by="company",
        )

        funds_release.delete_bank_refund_release.assert_called_once_with(inv.id)
        funds_release.create_bank_refund_release.assert_not_called()

    def test_delete_called_when_refunded_by_omitted_defaults_company(self):
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)
        uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
        )

        funds_release.delete_bank_refund_release.assert_called_once_with(inv.id)
        funds_release.create_bank_refund_release.assert_not_called()

    @pytest.mark.parametrize("status", ["refundable", "refund_pending", None])
    def test_delete_called_for_non_refunded_statuses(self, status):
        inv = _make_invoice(refundable_status="refunded", refunded_by="bank")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)
        uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status=status,
        )

        funds_release.delete_bank_refund_release.assert_called_once_with(inv.id)
        funds_release.create_bank_refund_release.assert_not_called()


class TestReconcileSkippedWhenPortIsNone:
    def test_no_reconcile_call_and_no_error_when_funds_release_omitted(self):
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)  # funds_release defaults to None
        result = uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by="bank",
        )

        assert result.refunded_by == "bank"  # status change itself still works


class TestReconcileOrderingAndAtomicity:
    def test_reconcile_runs_before_repo_update(self):
        """Reconcile must run before the status is persisted (repo.update()
        self-commits with no shared transaction — see the use-case docstring)."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()
        order: list[str] = []
        invoice_repo.update.side_effect = lambda i: (order.append("update"), i)[1]
        funds_release.create_bank_refund_release.side_effect = lambda *a, **k: order.append("create_release")

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)
        uc.execute(
            user_id=uuid4(),
            is_superadmin=True,
            invoice_id=inv.id,
            refundable_status="refunded",
            refunded_by="bank",
        )

        assert order == ["create_release", "update"]

    def test_reconcile_raising_prevents_status_persist(self):
        """If reconcile raises, repo.update() must never be called — the
        status change is never attempted, so there is nothing to roll back."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()
        funds_release.create_bank_refund_release.side_effect = RuntimeError("boom")

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        with pytest.raises(RuntimeError, match="boom"):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )

        invoice_repo.update.assert_not_called()

    def test_reconcile_delete_raising_prevents_status_persist(self):
        inv = _make_invoice(refundable_status="refunded", refunded_by="bank")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()
        funds_release.delete_bank_refund_release.side_effect = RuntimeError("boom")

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        with pytest.raises(RuntimeError, match="boom"):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refundable",
            )

        invoice_repo.update.assert_not_called()

    def test_repeating_the_same_patch_is_idempotent_at_the_wiring_level(self):
        """Two identical PATCHes each call create exactly once — idempotency
        itself is enforced by the adapter (phase 01/04), this test only proves
        the use-case calls it on every matching transition, not just the first."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        funds_release = _make_funds_release()
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        for _ in range(2):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )

        assert funds_release.create_bank_refund_release.call_count == 2


class TestReconcileUpdateFailureCompensation:
    """repo.update() commits separately from the reconcile step above it (see
    the use-case docstring) — these tests cover what happens when repo.update()
    is the one that fails, AFTER reconcile already committed."""

    def test_update_failure_after_create_deletes_the_release_and_reraises(self):
        """If repo.update() raises after create_bank_refund_release() already
        committed a release, execute() must compensate by deleting that
        release (best-effort — the two are separate transactions) and let the
        ORIGINAL repo.update() exception surface, never masking it."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        invoice_repo.update.side_effect = RuntimeError("update failed")
        funds_release = _make_funds_release()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        with pytest.raises(RuntimeError, match="update failed"):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )

        funds_release.create_bank_refund_release.assert_called_once()
        funds_release.delete_bank_refund_release.assert_called_once_with(inv.id)

    def test_update_failure_after_delete_cannot_compensate_but_still_reraises(self):
        """If repo.update() raises after delete_bank_refund_release() already
        removed the release, there is nothing left to compensate (recreating
        would mint a different FR number than the one just removed) —
        execute() must not call create, and the ORIGINAL exception must still
        surface."""
        inv = _make_invoice(refundable_status="refunded", refunded_by="bank")
        invoice_repo = _make_invoice_repo(inv)
        invoice_repo.update.side_effect = RuntimeError("update failed")
        funds_release = _make_funds_release()

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        with pytest.raises(RuntimeError, match="update failed"):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refundable",
            )

        funds_release.delete_bank_refund_release.assert_called_once_with(inv.id)
        funds_release.create_bank_refund_release.assert_not_called()

    def test_compensating_delete_failure_is_swallowed_and_original_exception_still_surfaces(self):
        """If the compensating delete itself raises, that failure must be
        swallowed (logged), not surfaced — the caller must see the ORIGINAL
        repo.update() exception, not an error from the cleanup attempt."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        invoice_repo.update.side_effect = RuntimeError("update failed")
        funds_release = _make_funds_release()
        funds_release.delete_bank_refund_release.side_effect = RuntimeError("compensation also failed")

        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo, funds_release=funds_release)

        with pytest.raises(RuntimeError, match="update failed"):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )


class TestMissingFundsReleaseIsObservable:
    def test_logs_a_warning_once_when_funds_release_is_none(self, caplog):
        """funds_release=None still works (backward compatible — see
        TestReconcileSkippedWhenPortIsNone) but must not be silent: a missing
        wire in production returns 200 with no release, which is only safe to
        ship if it is also observable."""
        inv = _make_invoice(refundable_status="refundable")
        invoice_repo = _make_invoice_repo(inv)
        uc = SetInvoiceRefundableStatusUseCase(invoice_repo=invoice_repo)  # funds_release defaults to None

        with caplog.at_level(logging.WARNING):
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )
            uc.execute(
                user_id=uuid4(),
                is_superadmin=True,
                invoice_id=inv.id,
                refundable_status="refunded",
                refunded_by="bank",
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1  # once per instance, not once per call
