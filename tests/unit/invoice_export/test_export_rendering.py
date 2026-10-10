"""What the expense export prints: free text as typed and wrapped, VAT lines that add up,
translated bank-refund lines, cent-rounded cells, cash advances apart, Paris time."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO

import openpyxl
from pypdf import PdfReader
from reportlab.platypus import Paragraph, Table

from app.domain.entities.invoice import InvoiceType
from app.domain.invoice.export.models import InvoiceBundle, TypeSubtotal
from app.domain.invoice.export.pdf_builder import _make_styles, _render_invoice_page, _render_invoices_table, build_pdf
from app.domain.invoice.export.xlsx_builder import EUR_FR_FORMAT, build_xlsx
from app.domain.value_objects.invoice_item import InvoiceItem

from tests.unit.invoice_export.test_pdf_builder import _make_bundle, _make_context, _make_invoice

USABLE_WIDTH = 500.0


def _text(pdf_bytes: bytes) -> str:
    return " ".join("".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf_bytes)).pages).split())


def _ctx(locale: str = "fr", **overrides):
    return dataclasses.replace(_make_context(), locale=locale, **overrides)


def _with_items(inv, *items: InvoiceItem):
    return inv.with_updates(items=list(items))


def _vat_invoice():
    return _with_items(
        _make_invoice(invoice_type=InvoiceType.MATERIALS_SERVICES, recipient="Dupont & Fils <SARL>"),
        InvoiceItem(description="Vis & chevilles <M8>", quantity=Decimal("3"), unit_price=Decimal("1.50"), vat_rate=20),
        InvoiceItem(description="Colle", quantity=Decimal("1"), unit_price=Decimal("0.123"), vat_rate=Decimal("20")),
    )


# ---------------------------------------------------------------------------
# PDF — free text
# ---------------------------------------------------------------------------


def test_pdf_prints_ampersands_and_brackets_as_typed():
    text = _text(build_pdf(_ctx(), _make_bundle([_vat_invoice()])))
    assert "Dupont & Fils <SARL>" in text
    assert "Vis & chevilles <M8>" in text
    assert "&amp;" not in text and "&lt;" not in text and "&gt;" not in text


def test_pdf_free_text_cells_wrap_inside_their_column():
    """Type, recipient and item description are Paragraphs (they wrap), never plain strings (they overflow)."""
    long_text = " ".join(["Fourniture et pose"] * 12)
    inv = _with_items(
        _make_invoice(recipient=long_text).with_updates(is_cash_advance=True),
        InvoiceItem(description=long_text, quantity=Decimal("1"), unit_price=Decimal("1")),
    )
    styles = _make_styles()

    invoices_table = next(e for e in _render_invoices_table([inv], styles, USABLE_WIDTH, "fr") if isinstance(e, Table))
    type_cell, recipient_cell = invoices_table._cellvalues[1][2], invoices_table._cellvalues[1][3]
    assert isinstance(type_cell, Paragraph) and type_cell.getPlainText() == "Autres (avance de trésorerie)"
    assert isinstance(recipient_cell, Paragraph) and recipient_cell.getPlainText() == long_text
    # The table as laid out stays within the page's usable width.
    assert invoices_table.wrap(USABLE_WIDTH, 800)[0] <= USABLE_WIDTH + 0.01

    page = _render_invoice_page(inv, _ctx(), styles, USABLE_WIDTH)
    header, meta, items_table, totals = [e for e in page if isinstance(e, Table)]
    description = items_table._cellvalues[1][0]
    assert isinstance(description, Paragraph) and description.getPlainText() == long_text
    assert items_table.wrap(USABLE_WIDTH, 800)[0] <= USABLE_WIDTH + 0.01


# ---------------------------------------------------------------------------
# PDF — VAT
# ---------------------------------------------------------------------------


def test_pdf_shows_vat_column_and_ht_vat_ttc_rows_when_a_line_has_vat():
    # 3 x 1,50 + 1 x 0,123 = 4,623 HT -> 4,62 € ; TTC 5,5476 -> 5,55 € ; VAT = 5,55 - 4,62 = 0,93 €
    text = _text(build_pdf(_ctx("fr"), _make_bundle([_vat_invoice()])))
    assert "TVA %" in text
    assert "20 %" in text
    assert "Total HT 4,62 €" in text
    assert "TVA 0,93 €" in text
    assert "Total TTC 5,55 €" in text


def test_pdf_has_no_vat_column_without_vat():
    text = _text(build_pdf(_ctx("fr"), _make_bundle([_make_invoice()])))
    assert "TVA %" not in text and "Total HT" not in text
    assert "TOTAL 250,00 €" in text


# ---------------------------------------------------------------------------
# PDF — bank refund line and labels
# ---------------------------------------------------------------------------


def _bank_refund_release():
    return _with_items(
        _make_invoice(invoice_type=InvoiceType.RELEASED_FUNDS, recipient="Banque").with_updates(is_auto_generated=True),
        InvoiceItem(description="Remboursement banque — INV-2026-0002", quantity=Decimal("1"), unit_price=Decimal("5")),
    )


def test_pdf_translates_the_auto_bank_refund_line():
    bundle = _make_bundle([_bank_refund_release()])
    assert "Ngân hàng hoàn tiền — INV-2026-0002" in _text(build_pdf(_ctx("vi"), bundle))
    en = _text(build_pdf(_ctx("en"), bundle))
    assert "Bank refund — INV-2026-0002" in en and "Remboursement banque" not in en
    assert "Remboursement banque — INV-2026-0002" in _text(build_pdf(_ctx("fr"), bundle))


def test_pdf_keeps_a_typed_line_that_only_looks_like_a_bank_refund():
    """Only the auto-generated release is translated; a user's own line is printed as typed."""
    typed = _bank_refund_release().with_updates(is_auto_generated=False)
    assert "Remboursement banque — INV-2026-0002" in _text(build_pdf(_ctx("en"), _make_bundle([typed])))


def test_vi_released_funds_wording_matches_the_app():
    text = _text(build_pdf(_ctx("vi"), _make_bundle([_make_invoice()])))
    assert "Vốn giải ngân" in text and "Vốn đã giải ngân" in text
    assert "Tiền giải ngân" not in text and "Tiền đã giải ngân" not in text


# ---------------------------------------------------------------------------
# Time and period
# ---------------------------------------------------------------------------


def test_pdf_prints_paris_time_and_month_names():
    # 18:41 UTC in October is 20:41 in Paris (summer time).
    ctx = _ctx("fr", generated_at=datetime(2026, 10, 9, 18, 41, tzinfo=timezone.utc))
    text = _text(build_pdf(ctx, _make_bundle([_make_invoice()])))
    assert "Généré le 09/10/2026 20:41" in text
    assert "18:41" not in text
    assert "Période : janv. 2026 à mars 2026" in text


def test_xlsx_prints_paris_time_and_month_names():
    # 23:30 UTC on 31 Dec is already 00:30 on 1 Jan in Paris (winter time).
    ctx = _ctx("fr", generated_at=datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc))
    wb = openpyxl.load_workbook(BytesIO(build_xlsx(ctx, _make_bundle([_make_invoice()]))))
    meta = wb["Synthèse"]["A2"].value
    assert meta == "Période : janv. 2026 à mars 2026 · Généré le 01/01/2027 00:30 par admin@example.com"


# ---------------------------------------------------------------------------
# XLSX — cents and cash advances
# ---------------------------------------------------------------------------


def test_xlsx_money_cells_are_rounded_to_the_cent():
    inv = _with_items(
        _make_invoice(invoice_type=InvoiceType.OTHERS, recipient="Divers"),
        InvoiceItem(description="x", quantity=Decimal("3"), unit_price=Decimal("33.333")),
    )
    bundle = InvoiceBundle(
        invoices=[inv],
        subtotals_by_type=[TypeSubtotal(type=InvoiceType.OTHERS, invoice_count=1, total_amount=inv.total_amount)],
        grand_total=inv.total_amount,
        invoice_count=1,
    )
    wb = openpyxl.load_workbook(BytesIO(build_xlsx(_ctx("en"), bundle)))
    money = [
        c.value
        for ws in wb
        for row in ws.iter_rows()
        for c in row
        if c.number_format == EUR_FR_FORMAT and isinstance(c.value, (int, float))
    ]
    # 99.999 everywhere it is shown (row, subtotal, total, sheet total); 0 is the released-funds KPI.
    assert set(money) == {0, 100}, money


def test_xlsx_cash_advance_has_its_own_subtotal_and_sheet():
    advance = _make_invoice(amount=Decimal("300.00"), recipient="Chef").with_updates(is_cash_advance=True)
    other = _make_invoice(invoice_type=InvoiceType.OTHERS, amount=Decimal("40.00"), recipient="Divers")
    bundle = InvoiceBundle(
        invoices=[advance, other],
        subtotals_by_type=[
            TypeSubtotal(type=InvoiceType.OTHERS, invoice_count=1, total_amount=Decimal("40.00")),
            TypeSubtotal(
                type=InvoiceType.OTHERS, invoice_count=1, total_amount=Decimal("300.00"), is_cash_advance=True
            ),
        ],
        grand_total=Decimal("40.00"),
        invoice_count=2,
    )
    wb = openpyxl.load_workbook(BytesIO(build_xlsx(_ctx("en"), bundle)))

    rows = list(wb["Summary"].iter_rows(values_only=True))
    assert ("Others", 1, 40) in [r[:3] for r in rows]
    assert ("Others (cash advance)", 1, 300) in [r[:3] for r in rows]

    assert wb.sheetnames[1:] == ["Others invoices", "Others (cash advance) invoices"]
    others_rows = list(wb["Others invoices"].iter_rows(values_only=True))
    assert "Chef" not in {c for r in others_rows for c in r}
    assert others_rows[-1][0] == "TOTAL" and others_rows[-1][-1] == 40
    advance_rows = list(wb["Others (cash advance) invoices"].iter_rows(values_only=True))
    assert advance_rows[-1][0] == "TOTAL" and advance_rows[-1][-1] == 300


def test_pdf_cash_advance_subtotal_is_labelled():
    advance = _make_invoice(amount=Decimal("300.00"), recipient="Chef").with_updates(is_cash_advance=True)
    bundle = InvoiceBundle(
        invoices=[advance],
        subtotals_by_type=[
            TypeSubtotal(type=InvoiceType.OTHERS, invoice_count=1, total_amount=Decimal("300"), is_cash_advance=True)
        ],
        grand_total=Decimal("0"),
        invoice_count=1,
    )
    text = _text(
        build_pdf(_ctx("en", range=dataclasses.replace(_make_context().range, to_month=date(2026, 1, 1))), bundle)
    )
    assert "Others (cash advance) 1 300,00 €" in text
