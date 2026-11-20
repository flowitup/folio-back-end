"""A facture converted from a devis prints "Suivant devis n° … du …" in its meta block."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from pypdf import PdfReader

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from app.domain.billing.value_objects import BillingDocumentItem
from app.infrastructure.pdf.billing_document_pdf_renderer import ReportLabBillingDocumentPdfRenderer


def _doc(kind: BillingDocumentKind, **overrides) -> BillingDocument:
    fields = dict(
        id=uuid4(),
        user_id=uuid4(),
        kind=kind,
        document_number="FAC-2026-001" if kind == BillingDocumentKind.FACTURE else "DEV-2026-004",
        status=BillingDocumentStatus.DRAFT,
        issue_date=date(2026, 9, 27),
        created_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 9, 27, tzinfo=timezone.utc),
        recipient_name="Client",
        issuer_legal_name="Lavandou Bâtiment SARL",
        issuer_address="9 rue du Test, 75011 Paris",
        items=(
            BillingDocumentItem(
                description="Service", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20")
            ),
        ),
    )
    fields.update(overrides)
    return BillingDocument(**fields)


def _text(doc: BillingDocument, source_devis: BillingDocument | None = None) -> str:
    pdf = ReportLabBillingDocumentPdfRenderer().render(doc, source_devis=source_devis)
    reader = PdfReader(BytesIO(pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text.replace(" ", " ").replace(" ", " ")


def test_converted_facture_prints_its_source_devis():
    devis = _doc(BillingDocumentKind.DEVIS, issue_date=date(2026, 9, 12))
    facture = _doc(BillingDocumentKind.FACTURE, source_devis_id=devis.id)
    assert "Suivant devis n° DEV-2026-004 du 12/09/2026" in _text(facture, source_devis=devis)


def test_facture_made_by_hand_has_no_source_devis_line():
    assert "Suivant devis" not in _text(_doc(BillingDocumentKind.FACTURE))


def test_deleted_source_devis_drops_the_line():
    # The devis is gone: the use case finds nothing and passes None.
    facture = _doc(BillingDocumentKind.FACTURE, source_devis_id=uuid4())
    assert "Suivant devis" not in _text(facture, source_devis=None)


def test_line_needs_the_devis_the_facture_came_from():
    other_devis = _doc(BillingDocumentKind.DEVIS)
    facture = _doc(BillingDocumentKind.FACTURE, source_devis_id=uuid4())
    assert "Suivant devis" not in _text(facture, source_devis=other_devis)


def test_devis_never_prints_the_line():
    devis = _doc(BillingDocumentKind.DEVIS)
    assert "Suivant devis" not in _text(_doc(BillingDocumentKind.DEVIS), source_devis=devis)


def test_terms_are_left_untouched():
    devis = _doc(BillingDocumentKind.DEVIS)
    facture = _doc(BillingDocumentKind.FACTURE, source_devis_id=devis.id, terms="Devis valable 30 jours.")
    assert "Devis valable 30 jours." in _text(facture, source_devis=devis)
