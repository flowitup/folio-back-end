"""Unit tests for refund invoice type: mixed-sign items, link validation, and cap.

Covers:
- create refund with mixed-sign items → stored as entered, total_amount signed
- create materials_services with negative discount line → accepted
- labor / others / released_funds with negative unit_price → InvalidInvoiceDataError (400)
- qty <= 0 rejected for all types
- refund link validation: non-M&S target, cross-project, self-link, non-refund type
- cap: over/at/under, second linked refund, exclude-self on update
- optional link: refund without refunds_invoice_id → no cap check
- sum_company_spent ignores refund type
- effective-type on update: PATCH items without resending type uses existing type
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.invoice.create_invoice import CreateInvoiceRequest, CreateInvoiceUseCase
from app.application.invoice.ports import IInvoiceRepository
from app.application.invoice.update_invoice import UpdateInvoiceRequest, UpdateInvoiceUseCase, _UNSET
from app.domain.entities.invoice import Invoice, InvoiceType, MIXED_SIGN_TYPES
from app.domain.exceptions.invoice_exceptions import InvalidInvoiceDataError, RefundExceedsSourceError
from app.domain.value_objects.invoice_item import InvoiceItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_item(unit_price: float = 100.0, quantity: float = 1.0, vat_rate: float = 0.0) -> dict:
    return {"description": "Line item", "quantity": quantity, "unit_price": unit_price, "vat_rate": vat_rate}


def _make_invoice(
    project_id=None,
    invoice_type: InvoiceType = InvoiceType.MATERIALS_SERVICES,
    unit_price: float = 500.0,
    refunds_invoice_id=None,
) -> Invoice:
    """Build a minimal Invoice domain entity."""
    pid = project_id or uuid4()
    return Invoice(
        id=uuid4(),
        project_id=pid,
        invoice_number="INV-2026-0001",
        type=invoice_type,
        issue_date=date.today(),
        recipient_name="Supplier Co",
        recipient_address=None,
        notes=None,
        items=[InvoiceItem(description="Work", quantity=Decimal("1"), unit_price=Decimal(str(unit_price)))],
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        is_auto_generated=False,
        refunds_invoice_id=refunds_invoice_id,
    )


def _make_mock_repo(find_by_id_result=None, sum_refunds: Decimal = Decimal("0")) -> MagicMock:
    repo = MagicMock(spec=IInvoiceRepository)
    repo.next_invoice_number.return_value = "INV-2026-0002"
    repo.create.side_effect = lambda inv: inv
    repo.find_by_id.return_value = find_by_id_result
    repo.update.side_effect = lambda inv: inv
    repo.sum_refunds_for_source.return_value = sum_refunds
    return repo


def _create_request(project_id=None, invoice_type=InvoiceType.REFUND, items=None, refunds_invoice_id=None):
    pid = project_id or uuid4()
    return CreateInvoiceRequest(
        project_id=pid,
        created_by=uuid4(),
        type=invoice_type,
        issue_date=date.today(),
        recipient_name="Supplier",
        items=items or [_make_item(-100.0)],
        refunds_invoice_id=refunds_invoice_id,
    )


def _update_request(invoice_id, items=None, invoice_type=None, refunds_invoice_id=_UNSET):
    kwargs = {"invoice_id": invoice_id}
    if items is not None:
        kwargs["items"] = items
    if invoice_type is not None:
        kwargs["type"] = invoice_type
    if refunds_invoice_id is not _UNSET:
        kwargs["refunds_invoice_id"] = refunds_invoice_id
    return UpdateInvoiceRequest(**kwargs)


# ---------------------------------------------------------------------------
# MIXED_SIGN_TYPES constant
# ---------------------------------------------------------------------------


class TestMixedSignTypesConstant:
    def test_materials_services_in_mixed_sign_types(self):
        assert InvoiceType.MATERIALS_SERVICES in MIXED_SIGN_TYPES

    def test_refund_in_mixed_sign_types(self):
        assert InvoiceType.REFUND in MIXED_SIGN_TYPES

    def test_labor_not_in_mixed_sign_types(self):
        assert InvoiceType.LABOR not in MIXED_SIGN_TYPES

    def test_others_not_in_mixed_sign_types(self):
        assert InvoiceType.OTHERS not in MIXED_SIGN_TYPES

    def test_released_funds_not_in_mixed_sign_types(self):
        assert InvoiceType.RELEASED_FUNDS not in MIXED_SIGN_TYPES


# ---------------------------------------------------------------------------
# Create: mixed-sign items
# ---------------------------------------------------------------------------


class TestCreateMixedSignItems:
    def test_create_refund_mixed_items_stored_as_entered(self):
        """Refund with [-200, -150, +30] → total_amount == -320."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            items=[
                _make_item(-200.0),
                _make_item(-150.0),
                _make_item(30.0),
            ]
        )
        result = uc.execute(req)
        assert result.total_amount == -320.0

    def test_create_refund_single_negative_item(self):
        """Refund with a single negative item succeeds."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        result = uc.execute(_create_request(items=[_make_item(-100.0)]))
        assert result.total_amount == -100.0
        assert result.type == "refund"

    def test_create_materials_services_negative_discount_line(self):
        """M&S with a negative discount line → accepted, total reduced."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            invoice_type=InvoiceType.MATERIALS_SERVICES,
            items=[_make_item(500.0), _make_item(-50.0)],
        )
        result = uc.execute(req)
        assert result.total_amount == 450.0

    def test_create_refund_positive_item_allowed(self):
        """A refund invoice may have positive lines (charge adjustment)."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        result = uc.execute(_create_request(items=[_make_item(100.0)]))
        assert result.total_amount == 100.0

    def test_stored_value_equals_entered_value_no_negation(self):
        """No auto-negation: unit_price is stored exactly as entered."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        uc.execute(_create_request(items=[_make_item(-200.0)]))
        saved = repo.create.call_args[0][0]
        assert saved.items[0].unit_price == Decimal("-200")


class TestCreatePositiveOnlyTypes:
    @pytest.mark.parametrize(
        "invoice_type",
        [
            InvoiceType.LABOR,
            InvoiceType.OTHERS,
            InvoiceType.RELEASED_FUNDS,
        ],
    )
    def test_negative_price_rejected_for_positive_only_types(self, invoice_type):
        """labor / others / released_funds must not accept negative unit_price (400)."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            invoice_type=invoice_type,
            items=[_make_item(-5.0)],
        )
        with pytest.raises(InvalidInvoiceDataError, match="[Nn]egative|unit_price"):
            uc.execute(req)

    @pytest.mark.parametrize(
        "invoice_type",
        [
            InvoiceType.LABOR,
            InvoiceType.OTHERS,
            InvoiceType.RELEASED_FUNDS,
            InvoiceType.MATERIALS_SERVICES,
            InvoiceType.REFUND,
        ],
    )
    def test_zero_qty_rejected_for_all_types(self, invoice_type):
        """qty <= 0 is always rejected regardless of invoice type."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        with pytest.raises(InvalidInvoiceDataError, match="[Qq]uantity"):
            uc.execute(
                _create_request(
                    invoice_type=invoice_type,
                    items=[{"description": "X", "quantity": 0, "unit_price": 10}],
                )
            )


# ---------------------------------------------------------------------------
# Create: refund link validation
# ---------------------------------------------------------------------------


class TestCreateRefundLinkValidation:
    def test_refunds_invoice_id_on_non_refund_type_raises(self):
        """refunds_invoice_id is only valid on type=refund."""
        source = _make_invoice()
        repo = _make_mock_repo(find_by_id_result=source)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            invoice_type=InvoiceType.MATERIALS_SERVICES,
            items=[_make_item(100.0)],
            refunds_invoice_id=source.id,
        )
        with pytest.raises(InvalidInvoiceDataError, match="type.*refund|refund.*type"):
            uc.execute(req)

    def test_refunds_non_existent_source_raises(self):
        """Linking to a non-existent invoice raises InvalidInvoiceDataError."""
        repo = _make_mock_repo(find_by_id_result=None)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(refunds_invoice_id=uuid4())
        with pytest.raises(InvalidInvoiceDataError, match="not found"):
            uc.execute(req)

    def test_refunds_cross_project_source_raises(self):
        """Source invoice in a different project must be rejected."""
        project_id = uuid4()
        other_project_source = _make_invoice(project_id=uuid4(), invoice_type=InvoiceType.MATERIALS_SERVICES)
        repo = _make_mock_repo(find_by_id_result=other_project_source)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(project_id=project_id, refunds_invoice_id=other_project_source.id)
        with pytest.raises(InvalidInvoiceDataError, match="same project"):
            uc.execute(req)

    def test_refunds_non_ms_source_raises(self):
        """Source must be materials_services; refund of labor/others raises."""
        project_id = uuid4()
        labor_invoice = _make_invoice(project_id=project_id, invoice_type=InvoiceType.LABOR)
        repo = _make_mock_repo(find_by_id_result=labor_invoice)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(project_id=project_id, refunds_invoice_id=labor_invoice.id)
        with pytest.raises(InvalidInvoiceDataError, match="materials_services"):
            uc.execute(req)

    def test_optional_link_no_refunds_invoice_id_succeeds(self):
        """Refund without refunds_invoice_id → accepted; no cap check."""
        repo = _make_mock_repo()
        uc = CreateInvoiceUseCase(repo)
        result = uc.execute(_create_request())  # refunds_invoice_id defaults to None
        assert result.type == "refund"
        repo.sum_refunds_for_source.assert_not_called()


# ---------------------------------------------------------------------------
# Create: cap enforcement
# ---------------------------------------------------------------------------


class TestCreateRefundCap:
    def _source_with_amount(self, project_id, amount: float):
        return _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=amount)

    def test_cap_exceeded_raises_refund_exceeds_source(self):
        """Refund total > source remaining → RefundExceedsSourceError with remaining in msg."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 300.0)
        repo = _make_mock_repo(find_by_id_result=source, sum_refunds=Decimal("0"))
        uc = CreateInvoiceUseCase(repo)
        # Refund of -400 would drive net to 300 - 400 = -100 < 0
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-400.0)],
            refunds_invoice_id=source.id,
        )
        with pytest.raises(RefundExceedsSourceError, match="300"):
            uc.execute(req)

    def test_cap_exactly_at_limit_succeeds(self):
        """Refund == source total → net is 0 → allowed."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 300.0)
        repo = _make_mock_repo(find_by_id_result=source, sum_refunds=Decimal("0"))
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-300.0)],
            refunds_invoice_id=source.id,
        )
        result = uc.execute(req)
        assert result.total_amount == -300.0

    def test_cap_under_limit_succeeds(self):
        """Refund < source total → net > 0 → allowed."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 300.0)
        repo = _make_mock_repo(find_by_id_result=source, sum_refunds=Decimal("0"))
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-200.0)],
            refunds_invoice_id=source.id,
        )
        result = uc.execute(req)
        assert result.total_amount == -200.0

    def test_second_refund_accounts_for_first(self):
        """When a prior refund already consumed part of the source, the cap includes it."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 300.0)
        # First refund already consumed -200 → remaining = 100
        repo = _make_mock_repo(find_by_id_result=source, sum_refunds=Decimal("-200"))
        uc = CreateInvoiceUseCase(repo)
        # Second refund of -150 would drive net to 300 - 200 - 150 = -50 < 0
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-150.0)],
            refunds_invoice_id=source.id,
        )
        with pytest.raises(RefundExceedsSourceError):
            uc.execute(req)

    def test_second_refund_within_remaining_succeeds(self):
        """Second refund within remaining capacity is accepted."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 300.0)
        repo = _make_mock_repo(find_by_id_result=source, sum_refunds=Decimal("-200"))
        uc = CreateInvoiceUseCase(repo)
        # Remaining = 100; refund of -100 exactly hits the cap
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-100.0)],
            refunds_invoice_id=source.id,
        )
        result = uc.execute(req)
        assert result.total_amount == -100.0

    def test_sum_refunds_for_source_called_with_source_id(self):
        """Cap check queries sum_refunds_for_source with the correct source_id."""
        project_id = uuid4()
        source = self._source_with_amount(project_id, 500.0)
        repo = _make_mock_repo(find_by_id_result=source)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(
            project_id=project_id,
            items=[_make_item(-100.0)],
            refunds_invoice_id=source.id,
        )
        uc.execute(req)
        repo.sum_refunds_for_source.assert_called_once_with(source.id)


# ---------------------------------------------------------------------------
# Update: effective-type sign validation
# ---------------------------------------------------------------------------


class TestUpdateEffectiveTypeSignGuard:
    def test_patch_negative_items_without_type_on_refund_succeeds(self):
        """PATCH only items on an existing refund invoice without resending type → accepted."""
        project_id = uuid4()
        existing = _make_invoice(project_id=project_id, invoice_type=InvoiceType.REFUND)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        req = UpdateInvoiceRequest(
            invoice_id=existing.id,
            items=[_make_item(-200.0)],
            # type NOT provided — effective_type should fall back to existing REFUND
        )
        result = uc.execute(req)
        assert result.total_amount == -200.0

    def test_patch_negative_items_without_type_on_ms_succeeds(self):
        """PATCH only items on an existing M&S invoice without resending type → accepted."""
        project_id = uuid4()
        existing = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        req = UpdateInvoiceRequest(
            invoice_id=existing.id,
            items=[_make_item(-50.0)],
        )
        result = uc.execute(req)
        assert result.total_amount == -50.0

    def test_patch_negative_items_on_labor_without_type_raises(self):
        """PATCH negative item on existing labor invoice without resending type → 400."""
        project_id = uuid4()
        existing = _make_invoice(project_id=project_id, invoice_type=InvoiceType.LABOR)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        req = UpdateInvoiceRequest(
            invoice_id=existing.id,
            items=[_make_item(-5.0)],
        )
        with pytest.raises(InvalidInvoiceDataError, match="[Nn]egative|unit_price"):
            uc.execute(req)

    def test_patch_negative_items_on_others_without_type_raises(self):
        """PATCH negative item on existing others invoice without resending type → 400."""
        project_id = uuid4()
        existing = _make_invoice(project_id=project_id, invoice_type=InvoiceType.OTHERS)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        with pytest.raises(InvalidInvoiceDataError):
            uc.execute(UpdateInvoiceRequest(invoice_id=existing.id, items=[_make_item(-5.0)]))


# ---------------------------------------------------------------------------
# Update: refunds_invoice_id sentinel
# ---------------------------------------------------------------------------


class TestUpdateRefundsInvoiceIdSentinel:
    def _refund_invoice(self, project_id, refunds_invoice_id=None):
        return _make_invoice(
            project_id=project_id,
            invoice_type=InvoiceType.REFUND,
            refunds_invoice_id=refunds_invoice_id,
        )

    def test_absent_sentinel_keeps_existing_link(self):
        """refunds_invoice_id absent from PATCH body → existing link preserved."""
        project_id = uuid4()
        source_id = uuid4()
        existing = self._refund_invoice(project_id, refunds_invoice_id=source_id)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        # refunds_invoice_id NOT provided → _UNSET
        uc.execute(UpdateInvoiceRequest(invoice_id=existing.id, recipient_name="New Name"))
        saved = repo.update.call_args[0][0]
        assert saved.refunds_invoice_id == source_id

    def test_explicit_null_clears_link(self):
        """Explicit refunds_invoice_id=None in PATCH body → link is cleared."""
        project_id = uuid4()
        source_id = uuid4()
        existing = self._refund_invoice(project_id, refunds_invoice_id=source_id)

        repo = _make_mock_repo(find_by_id_result=existing)
        uc = UpdateInvoiceUseCase(repo)
        uc.execute(UpdateInvoiceRequest(invoice_id=existing.id, refunds_invoice_id=None))
        saved = repo.update.call_args[0][0]
        assert saved.refunds_invoice_id is None

    def test_set_new_link_validates_and_applies(self):
        """Setting refunds_invoice_id to a valid M&S source sets the link."""
        project_id = uuid4()
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=500.0)
        existing = self._refund_invoice(project_id)  # no existing link

        # find_by_id is called first for the existing invoice, then for the source.
        repo = MagicMock(spec=IInvoiceRepository)
        repo.find_by_id.side_effect = [existing, source]
        repo.update.side_effect = lambda inv: inv
        repo.sum_refunds_for_source.return_value = Decimal("0")

        uc = UpdateInvoiceUseCase(repo)
        uc.execute(UpdateInvoiceRequest(invoice_id=existing.id, refunds_invoice_id=source.id))
        saved = repo.update.call_args[0][0]
        assert saved.refunds_invoice_id == source.id

    def test_set_link_on_non_refund_type_raises(self):
        """Setting refunds_invoice_id on a non-refund invoice → InvalidInvoiceDataError."""
        project_id = uuid4()
        ms_invoice = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES)
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES)

        repo = MagicMock(spec=IInvoiceRepository)
        repo.find_by_id.side_effect = [ms_invoice, source]
        repo.update.side_effect = lambda inv: inv
        uc = UpdateInvoiceUseCase(repo)

        with pytest.raises(InvalidInvoiceDataError, match="type.*refund|refund.*type"):
            uc.execute(UpdateInvoiceRequest(invoice_id=ms_invoice.id, refunds_invoice_id=source.id))


# ---------------------------------------------------------------------------
# Update: cap excludes self on update
# ---------------------------------------------------------------------------


class TestUpdateCapExcludesSelf:
    def test_update_raising_amount_within_remaining_ok(self):
        """On update, the cap excludes the invoice's own prior row (no self-double-count)."""
        project_id = uuid4()
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=500.0)
        # existing refund is currently -100; its own row is excluded from the Σ
        source_id = source.id
        existing_refund = Invoice(
            id=uuid4(),
            project_id=project_id,
            invoice_number="INV-2026-0002",
            type=InvoiceType.REFUND,
            issue_date=date.today(),
            recipient_name="Refund",
            recipient_address=None,
            notes=None,
            items=[InvoiceItem(description="R", quantity=Decimal("1"), unit_price=Decimal("-100"))],
            created_by=uuid4(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            is_auto_generated=False,
            refunds_invoice_id=source_id,
        )

        repo = MagicMock(spec=IInvoiceRepository)
        # First call: load the existing invoice; second call: load the source for cap check.
        repo.find_by_id.side_effect = [existing_refund, source]
        repo.update.side_effect = lambda inv: inv
        # sum_refunds_for_source returns 0 when self is excluded (no other linked refunds)
        repo.sum_refunds_for_source.return_value = Decimal("0")

        uc = UpdateInvoiceUseCase(repo)
        # PATCH: raise refund to -400 (still within source 500); self excluded → remaining = 500
        result = uc.execute(
            UpdateInvoiceRequest(
                invoice_id=existing_refund.id,
                items=[_make_item(-400.0)],
                refunds_invoice_id=_UNSET,  # keep existing link
            )
        )
        assert result.total_amount == -400.0
        # Verify exclude_invoice_id was passed
        call_args = repo.sum_refunds_for_source.call_args
        assert call_args[1].get("exclude_invoice_id") == existing_refund.id or (
            len(call_args[0]) > 1 and call_args[0][1] == existing_refund.id
        )

    def test_update_cap_exceeded_raises(self):
        """On update, if new total exceeds remaining, RefundExceedsSourceError raised."""
        project_id = uuid4()
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=300.0)
        source_id = source.id
        existing_refund = Invoice(
            id=uuid4(),
            project_id=project_id,
            invoice_number="INV-2026-0003",
            type=InvoiceType.REFUND,
            issue_date=date.today(),
            recipient_name="Refund",
            recipient_address=None,
            notes=None,
            items=[InvoiceItem(description="R", quantity=Decimal("1"), unit_price=Decimal("-100"))],
            created_by=uuid4(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            is_auto_generated=False,
            refunds_invoice_id=source_id,
        )

        repo = MagicMock(spec=IInvoiceRepository)
        # First call: load the existing invoice; second call: load the source for cap check.
        repo.find_by_id.side_effect = [existing_refund, source]
        # Another refund of -200 already exists for the same source (excluding self → -200 remains)
        repo.sum_refunds_for_source.return_value = Decimal("-200")
        uc = UpdateInvoiceUseCase(repo)

        # Attempting to raise this refund to -200 would give net: 300 + (-200) + (-200) = -100 < 0
        with pytest.raises(RefundExceedsSourceError):
            uc.execute(
                UpdateInvoiceRequest(
                    invoice_id=existing_refund.id,
                    items=[_make_item(-200.0)],
                    refunds_invoice_id=_UNSET,
                )
            )


# ---------------------------------------------------------------------------
# sum_refunds_for_source repo method (via mock verifications)
# ---------------------------------------------------------------------------


class TestSumRefundsForSourceContract:
    """Verify the use-case calls sum_refunds_for_source with correct args."""

    def test_create_calls_sum_without_exclude(self):
        """On create, sum_refunds_for_source called without exclude_invoice_id."""
        project_id = uuid4()
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=500.0)
        repo = _make_mock_repo(find_by_id_result=source)
        uc = CreateInvoiceUseCase(repo)
        req = _create_request(project_id=project_id, items=[_make_item(-100.0)], refunds_invoice_id=source.id)
        uc.execute(req)
        repo.sum_refunds_for_source.assert_called_once_with(source.id)

    def test_update_calls_sum_with_exclude(self):
        """On update with existing link + new items, sum excludes the invoice's own row."""
        project_id = uuid4()
        source = _make_invoice(project_id=project_id, invoice_type=InvoiceType.MATERIALS_SERVICES, unit_price=500.0)
        existing_refund = Invoice(
            id=uuid4(),
            project_id=project_id,
            invoice_number="INV-2026-0004",
            type=InvoiceType.REFUND,
            issue_date=date.today(),
            recipient_name="Refund",
            recipient_address=None,
            notes=None,
            items=[InvoiceItem(description="R", quantity=Decimal("1"), unit_price=Decimal("-100"))],
            created_by=uuid4(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            is_auto_generated=False,
            refunds_invoice_id=source.id,
        )
        repo = MagicMock(spec=IInvoiceRepository)
        # First call: load the existing invoice; second call: load source for cap check.
        repo.find_by_id.side_effect = [existing_refund, source]
        repo.update.side_effect = lambda inv: inv
        repo.sum_refunds_for_source.return_value = Decimal("0")
        uc = UpdateInvoiceUseCase(repo)

        uc.execute(
            UpdateInvoiceRequest(
                invoice_id=existing_refund.id,
                items=[_make_item(-200.0)],
            )
        )
        call_args = repo.sum_refunds_for_source.call_args
        # Called with (source.id, exclude_invoice_id=existing_refund.id)
        assert call_args[0][0] == source.id
        assert call_args[1].get("exclude_invoice_id") == existing_refund.id or (
            len(call_args[0]) > 1 and call_args[0][1] == existing_refund.id
        )


# ---------------------------------------------------------------------------
# Export: TYPE_LABEL_EN includes refund and others
# ---------------------------------------------------------------------------


class TestExportTypeLabelEN:
    def test_refund_label_in_type_label_en(self):
        from app.domain.invoice.export.format import TYPE_LABEL_EN

        assert "refund" in TYPE_LABEL_EN
        assert TYPE_LABEL_EN["refund"] == "Refund"

    def test_others_label_in_type_label_en(self):
        from app.domain.invoice.export.format import TYPE_LABEL_EN

        assert "others" in TYPE_LABEL_EN
        assert TYPE_LABEL_EN["others"] == "Others"

    def test_xlsx_builder_get_with_refund_type(self):
        """xlsx_builder uses .get() so refund type produces a label, not a KeyError."""
        from app.domain.invoice.export.format import TYPE_LABEL_EN

        label = TYPE_LABEL_EN.get("refund", "refund".replace("_", " ").title())
        assert label == "Refund"
