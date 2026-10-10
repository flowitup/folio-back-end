"""A billing document's default issue date, and the year in its number, follow the
business (Paris) calendar, not UTC's: at 00:30 on 1 January in Paris the UTC day is
still 31 December, and a facture made then must be dated 2027-01-01 and numbered 2027."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import app.application.billing._helpers as helpers_module
import app.application.billing.import_billing_document_usecase as import_module
import app.domain.time as time_module
from app.application.billing._helpers import _build_doc_from_inputs
from app.application.billing.apply_template_to_create_document_usecase import ApplyTemplateToCreateDocumentUseCase
from app.application.billing.clone_billing_document_usecase import CloneBillingDocumentUseCase
from app.application.billing.convert_devis_to_facture_usecase import ConvertDevisToFactureUseCase
from app.application.billing.create_billing_document_usecase import CreateBillingDocumentUseCase
from app.application.billing.dtos import (
    ApplyTemplateInput,
    CloneBillingDocumentInput,
    ConvertDevisToFactureInput,
    CreateBillingDocumentInput,
    ImportBillingDocumentInput,
    ItemInput,
)
from app.application.billing.import_billing_document_usecase import ImportBillingDocumentUseCase
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from tests.unit.application.billing.conftest import make_doc, make_item, make_template

# 23:30 UTC on 31 December 2026 is 00:30 on 1 January 2027 in Paris.
_NEW_YEAR_NIGHT_UTC = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)
_PARIS_DAY = date(2027, 1, 1)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NEW_YEAR_NIGHT_UTC if tz is None else _NEW_YEAR_NIGHT_UTC.astimezone(tz)


@pytest.fixture(autouse=True)
def new_year_night(monkeypatch):
    for module in (time_module, helpers_module, import_module):
        monkeypatch.setattr(module, "datetime", _FrozenDatetime)


def _item():
    return ItemInput(description="Service", quantity=Decimal("1"), unit_price=Decimal("500"), vat_rate=Decimal("20"))


def _deps(doc_repo, counter_repo, company_repo, access_repo):
    return dict(
        doc_repo=doc_repo,
        counter_repo=counter_repo,
        project_repo=None,
        company_repo=company_repo,
        access_repo=access_repo,
    )


def test_create_without_issue_date_uses_the_paris_day_and_year(
    doc_repo, counter_repo, company_repo, access_repo, fake_session, user_id, company_id, seeded_company
):
    usecase = CreateBillingDocumentUseCase(**_deps(doc_repo, counter_repo, company_repo, access_repo))
    inp = CreateBillingDocumentInput(
        user_id=user_id,
        kind=BillingDocumentKind.FACTURE,
        recipient_name="Acme Corp",
        company_id=company_id,
        items=[_item()],
    )
    result = usecase.execute(inp, fake_session)
    assert result.issue_date == _PARIS_DAY
    assert "-2027-" in result.document_number
    assert result.payment_due_date == date(2027, 1, 31)


def test_convert_dates_and_numbers_the_facture_on_the_paris_day(
    doc_repo, counter_repo, company_repo, access_repo, fake_session, user_id, company_id, seeded_company
):
    devis = make_doc(
        user_id=user_id,
        kind=BillingDocumentKind.DEVIS,
        status=BillingDocumentStatus.ACCEPTED,
        company_id=company_id,
    )
    doc_repo.save(devis)
    usecase = ConvertDevisToFactureUseCase(**_deps(doc_repo, counter_repo, company_repo, access_repo))
    result = usecase.execute(ConvertDevisToFactureInput(source_devis_id=devis.id, user_id=user_id), fake_session)
    assert result.issue_date == _PARIS_DAY
    assert "FAC-2027-" in result.document_number


def test_clone_dates_and_numbers_the_copy_on_the_paris_day(
    doc_repo, counter_repo, company_repo, access_repo, fake_session, user_id, company_id, seeded_company
):
    source = make_doc(user_id=user_id, status=BillingDocumentStatus.SENT, company_id=company_id)
    doc_repo.save(source)
    usecase = CloneBillingDocumentUseCase(**_deps(doc_repo, counter_repo, company_repo, access_repo))
    result = usecase.execute(CloneBillingDocumentInput(source_id=source.id, user_id=user_id), fake_session)
    assert result.issue_date == _PARIS_DAY
    assert "DEV-2027-" in result.document_number


def test_apply_template_without_issue_date_uses_the_paris_day(
    doc_repo, template_repo, counter_repo, company_repo, access_repo, fake_session, user_id, company_id, seeded_company
):
    template = make_template(user_id=user_id, kind=BillingDocumentKind.DEVIS)
    template_repo.save(template)
    usecase = ApplyTemplateToCreateDocumentUseCase(
        template_repo=template_repo, **_deps(doc_repo, counter_repo, company_repo, access_repo)
    )
    inp = ApplyTemplateInput(
        template_id=template.id, user_id=user_id, recipient_name="Client Corp", company_id=company_id
    )
    result = usecase.execute(inp, fake_session)
    assert result.issue_date == _PARIS_DAY
    assert "DEV-2027-" in result.document_number


def test_import_without_issue_date_uses_the_paris_day(
    doc_repo, counter_repo, company_repo, access_repo, fake_session, user_id, company_id, seeded_company
):
    usecase = ImportBillingDocumentUseCase(
        doc_repo=doc_repo, counter_repo=counter_repo, company_repo=company_repo, access_repo=access_repo
    )
    inp = ImportBillingDocumentInput(
        user_id=user_id,
        kind=BillingDocumentKind.FACTURE,
        recipient_name="Client Corp",
        items=[_item()],
        document_number="FAC-OLD-1",
        status=BillingDocumentStatus.PAID,
        company_id=company_id,
    )
    result = usecase.execute(inp, fake_session)
    assert result.issue_date == _PARIS_DAY


def test_build_doc_without_issue_date_uses_the_paris_day(user_id):
    doc = _build_doc_from_inputs(
        user_id=user_id,
        kind=BillingDocumentKind.DEVIS,
        document_number="DEV-2027-001",
        issuer_snapshot={"issuer_legal_name": "Test Company SAS", "issuer_address": "1 rue de la Paix"},
        recipient_name="Client Corp",
        items=(make_item(),),
    )
    assert doc.issue_date == _PARIS_DAY
    assert doc.validity_until == date(2027, 1, 31)
