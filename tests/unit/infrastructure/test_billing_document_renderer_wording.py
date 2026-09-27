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
from app.domain.labor.export.format import format_decimal_fr
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


@pytest.mark.parametrize(
    "value,text",
    [
        (Decimal("1.5"), "1,5"),
        (Decimal("5.50"), "5,5"),
        (Decimal("20"), "20"),
        (10.0, "10"),
        (1234.5, "1 234,5"),
        (Decimal("-0.5"), "-0,5"),
        (None, "—"),
    ],
)
def test_format_decimal_fr_uses_a_decimal_comma(value, text):
    assert format_decimal_fr(value) == text


def test_pdf_prints_quantities_and_vat_rates_with_a_decimal_comma():
    from pypdf import PdfReader

    from app.infrastructure.pdf.billing_document_pdf_renderer import ReportLabBillingDocumentPdfRenderer

    doc = _doc(
        BillingDocumentKind.FACTURE,
        items=(
            BillingDocumentItem(
                description="Peinture", quantity=Decimal("2.5"), unit_price=Decimal("10"), vat_rate=Decimal("5.5")
            ),
        ),
    )
    pdf = ReportLabBillingDocumentPdfRenderer().render(doc)
    text = "".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages).replace(" ", " ")
    assert "2,5" in text
    assert "TVA 5,5 %" in text
    assert "2.5" not in text and "5.5" not in text


def test_invoice_export_quantity_follows_the_export_language():
    from app.domain.invoice.export.pdf_builder import _format_quantity

    assert _format_quantity(10.0, "fr") == "10"
    assert _format_quantity(1.5, "fr") == "1,5"
    assert _format_quantity(1.5, "vi") == "1,5"
    assert _format_quantity(1.5, "en") == "1.5"


def test_xlsx_lines_after_a_section_without_one_get_their_own_heading():
    def line(desc, category=None):
        return BillingDocumentItem(
            description=desc,
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            vat_rate=Decimal("20"),
            category=category,
        )

    texts = _sheet_texts(
        _doc(BillingDocumentKind.DEVIS, items=(line("Coffrage", "Gros oeuvre"), line("Nettoyage de fin de chantier")))
    )
    assert texts.index("Gros oeuvre") < texts.index("Coffrage") < texts.index("Divers")
    assert texts.index("Divers") < texts.index("Nettoyage de fin de chantier")
