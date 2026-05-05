"""Unit tests for CreateBillingDocumentUseCase."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.create_billing_document_usecase import CreateBillingDocumentUseCase
from app.application.billing.dtos import CreateBillingDocumentInput, ItemInput
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import MissingCompanyProfileError
from tests.unit.application.billing.conftest import make_profile


@pytest.fixture
def usecase(doc_repo, counter_repo, profile_repo):
    return CreateBillingDocumentUseCase(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        profile_repo=profile_repo,
    )


def _inp(user_id, kind=BillingDocumentKind.DEVIS, **overrides):
    defaults = dict(
        user_id=user_id,
        kind=kind,
        recipient_name="Acme Corp",
        items=[
            ItemInput(description="Service", quantity=Decimal("1"), unit_price=Decimal("500"), vat_rate=Decimal("20"))
        ],
    )
    defaults.update(overrides)
    return CreateBillingDocumentInput(**defaults)


class TestCreateBillingDocumentHappyPath:
    def test_creates_devis_returns_response(self, usecase, doc_repo, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id), fake_session)
        assert result.kind == "devis"
        assert result.status == "draft"
        assert result.recipient_name == "Acme Corp"
        assert result.issuer_legal_name == profile.legal_name
        # Stored in repo
        assert doc_repo.find_by_id(result.id) is not None

    def test_creates_facture(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id, kind=BillingDocumentKind.FACTURE), fake_session)
        assert result.kind == "facture"

    def test_document_number_generated(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id), fake_session)
        assert "DEV" in result.document_number or "FAC" in result.document_number
        assert "2026" in result.document_number or str(date.today().year) in result.document_number

    def test_counter_increments_on_second_create(self, usecase, fake_session, user_id, profile):
        r1 = usecase.execute(_inp(user_id), fake_session)
        r2 = usecase.execute(_inp(user_id), fake_session)
        assert r1.document_number != r2.document_number

    def test_issuer_snapshot_copied_from_profile(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id), fake_session)
        assert result.issuer_legal_name == profile.legal_name
        assert result.issuer_address == profile.address

    def test_with_prefix_override(self, usecase, doc_repo, counter_repo, profile_repo, fake_session):
        uid = uuid4()
        p = make_profile(uid, prefix="FLW")
        profile_repo.save(p)
        from app.application.billing.create_billing_document_usecase import CreateBillingDocumentUseCase

        uc = CreateBillingDocumentUseCase(doc_repo=doc_repo, counter_repo=counter_repo, profile_repo=profile_repo)
        result = uc.execute(_inp(uid), fake_session)
        assert result.document_number.startswith("FLW-")

    def test_default_validity_until_set_for_devis(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id, issue_date=date(2026, 1, 1)), fake_session)
        assert result.validity_until is not None

    def test_default_payment_due_date_set_for_facture(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(
            _inp(user_id, kind=BillingDocumentKind.FACTURE, issue_date=date(2026, 1, 1)),
            fake_session,
        )
        assert result.payment_due_date is not None

    def test_totals_computed(self, usecase, fake_session, user_id, profile):
        result = usecase.execute(_inp(user_id), fake_session)
        assert result.total_ht == Decimal("500")
        assert result.total_tva == Decimal("100")
        assert result.total_ttc == Decimal("600")


class TestCreateBillingDocumentErrors:
    def test_missing_company_profile_raises(self, usecase, fake_session, user_id):
        # profile_repo is empty — no profile for user_id
        with pytest.raises(MissingCompanyProfileError) as exc_info:
            usecase.execute(_inp(user_id), fake_session)
        assert exc_info.value.user_id == user_id

    def test_empty_items_raises(self, usecase, fake_session, user_id, profile):
        with pytest.raises(ValueError, match="At least one line item"):
            usecase.execute(_inp(user_id, items=[]), fake_session)

    def test_empty_recipient_name_raises(self, usecase, fake_session, user_id, profile):
        with pytest.raises(ValueError, match="Recipient name"):
            usecase.execute(_inp(user_id, recipient_name="   "), fake_session)

    def test_negative_quantity_raises(self, usecase, fake_session, user_id, profile):
        bad_item = ItemInput(description="X", quantity=Decimal("-1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
        with pytest.raises(ValueError, match="quantity"):
            usecase.execute(_inp(user_id, items=[bad_item]), fake_session)

    def test_empty_item_description_raises(self, usecase, fake_session, user_id, profile):
        bad_item = ItemInput(description="  ", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20"))
        with pytest.raises(ValueError, match="description"):
            usecase.execute(_inp(user_id, items=[bad_item]), fake_session)
