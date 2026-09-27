"""French wording printed on billing document PDFs and XLSX sheets."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.domain.billing.document import BillingDocument
from app.domain.billing.document_wording import intro_sentence, place_of_issue
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.value_objects import BillingDocumentItem
from app.infrastructure.xlsx.billing_document_xlsx_renderer import OpenpyxlBillingDocumentXlsxRenderer


def _doc(kind: BillingDocumentKind, **overrides) -> BillingDocument:
    fields = dict(
        id=uuid4(),
        user_id=uuid4(),
        kind=kind,
        document_number="FAC-2026-001" if kind == BillingDocumentKind.FACTURE else "DEV-2026-001",
        status=BillingDocumentStatus.DRAFT,
        issue_date=date(2026, 9, 27),
        created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        recipient_name="Client",
        issuer_legal_name="QA-C Construction SARL",
        issuer_address="9 rue du Test, 75011 Paris",
        items=(
            BillingDocumentItem(
                description="Service", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20")
            ),
        ),
    )
    fields.update(overrides)
    return BillingDocument(**fields)


def _sheet_texts(doc: BillingDocument) -> list[str]:
    ws = load_workbook(BytesIO(OpenpyxlBillingDocumentXlsxRenderer().render(doc))).active
    return [str(c.value) for row in ws.iter_rows() for c in row if isinstance(c.value, str)]


@pytest.mark.parametrize(
    "kind,expected",
    [
        (BillingDocumentKind.FACTURE, "Veuillez trouver ci-après la facture relative à la mission citée en objet."),
        (BillingDocumentKind.DEVIS, "Veuillez trouver ci-après le devis relatif à la mission citée en objet."),
    ],
)
def test_intro_sentence_agrees_with_the_document_kind(kind, expected):
    assert intro_sentence(kind) == expected
    assert expected in _sheet_texts(_doc(kind))


@pytest.mark.parametrize(
    "address,city",
    [
        ("9 rue du Test, 75011 Paris, France", "Paris"),
        ("9 rue du Test, 75011 Paris", "Paris"),
        ("9 rue du Test, Paris, 75011", "Paris"),
        ("9 rue du Test\n75011 Paris", "Paris"),
        ("10 rue A, 69003 Lyon Cedex 03", "Lyon Cedex 03"),
        ("12 avenue Foch", ""),
        ("9 rue du Test, 75011", ""),
        (None, ""),
    ],
)
def test_place_of_issue_reads_the_city_after_the_postcode(address, city):
    assert place_of_issue(address) == city


def test_xlsx_date_line_names_the_city_not_the_country():
    texts = _sheet_texts(_doc(BillingDocumentKind.FACTURE, issuer_address="9 rue du Test, 75011 Paris, France"))
    assert "Paris, 27/09/2026" in texts
    assert "France, 27/09/2026" not in texts
    no_city = _sheet_texts(_doc(BillingDocumentKind.FACTURE, issuer_address="12 avenue Foch"))
    assert "27/09/2026" in no_city
