"""openpyxl renderer for BillingDocument entities.

Implements BillingDocumentXlsxRendererPort. Mirrors the source spreadsheet
layout used by ANN ECO CONSTRUCTION (and similar French construction-doc
formats):

  rows  1- 5: issuer header band (legal_name + address + SIRET + TVA + tel)
  row     8: "Réf Facture <number>" + "À l'attention de :"
  rows  9-11: recipient name + address (right column)
  row    13: project / object description
  row    17: "<City>, DD/MM/YYYY" issue date
  row    23: items header (Libellé | U | Qté | PU HT | Avancement | Montant HT | TVA)
  rows  24+: section header rows (col B only) interleaved with line items
  row N + 1: Total HT
  row N + 2: TVA
  row N + 3: Total TTC
  rows N+5 onward: bank coordinates + late-payment legal note

Returns raw bytes of an .xlsx file (Open Office XML, application/vnd.…).
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.domain.billing.document import BillingDocument
from app.domain.billing.enums import BillingDocumentKind
from app.domain.labor.export.format import format_eur_fr


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


def _bold(size: int = 10) -> Font:
    return Font(name="Calibri", size=size, bold=True)


def _normal(size: int = 10) -> Font:
    return Font(name="Calibri", size=size)


def _grey(size: int = 9) -> Font:
    return Font(name="Calibri", size=size, color="666666")


def _section_fill() -> PatternFill:
    return PatternFill(fill_type="solid", fgColor="F0F4FA")


def _header_fill() -> PatternFill:
    return PatternFill(fill_type="solid", fgColor="E8EDF5")


def _thin_border() -> Border:
    side = Side(style="thin", color="CCCCCC")
    return Border(left=side, right=side, top=side, bottom=side)


def _center() -> Alignment:
    return Alignment(horizontal="center", vertical="center", wrap_text=True)


def _left() -> Alignment:
    return Alignment(horizontal="left", vertical="top", wrap_text=True)


def _right() -> Alignment:
    return Alignment(horizontal="right", vertical="center", wrap_text=False)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class OpenpyxlBillingDocumentXlsxRenderer:
    """Implements BillingDocumentXlsxRendererPort via openpyxl.

    Stateless. Instantiate once and call render() per document.
    """

    def render(self, doc: BillingDocument) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Facture" if doc.kind == BillingDocumentKind.FACTURE else "Devis"

        # Column widths roughly tuned for the source layout.
        ws.column_dimensions["A"].width = 2
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = 60  # description
        ws.column_dimensions["D"].width = 8  # unit
        ws.column_dimensions["E"].width = 8  # qty
        ws.column_dimensions["F"].width = 14  # PU HT
        ws.column_dimensions["G"].width = 12  # avancement
        ws.column_dimensions["H"].width = 16  # Montant HT
        ws.column_dimensions["I"].width = 30  # right-column / TVA / recipient

        # ---- 1. Issuer header (rows 1-5) ----
        ws.cell(row=1, column=2, value=doc.issuer_legal_name).font = _bold(14)
        ws.cell(row=2, column=3, value=f"Siège : {doc.issuer_address or ''}").font = _normal(9)
        if doc.issuer_siret:
            ws.cell(row=4, column=3, value=f"SIRET : {doc.issuer_siret}").font = _normal(9)
        if doc.issuer_tva_number:
            ws.cell(row=5, column=3, value=f"TVA : {doc.issuer_tva_number}").font = _normal(9)

        # ---- 2. Doc number row (row 8) ----
        kind_label = "Facture" if doc.kind == BillingDocumentKind.FACTURE else "Devis"
        ws.cell(row=8, column=2, value=f"Réf {kind_label}").font = _bold(10)
        ws.cell(row=8, column=4, value=doc.document_number).font = _bold(11)
        ws.cell(row=8, column=8, value="À l'attention de :").font = _bold(10)

        # ---- 3. Recipient block (rows 9-12, right column I) ----
        ws.cell(row=9, column=9, value=doc.recipient_name).font = _bold(10)
        if doc.recipient_address:
            for offset, line in enumerate(doc.recipient_address.splitlines(), start=10):
                if offset > 12:
                    break
                ws.cell(row=offset, column=9, value=line).font = _normal(9)

        # ---- 4. Project / object (rows 12-14) ----
        ws.cell(row=12, column=2, value="Objet/Opération").font = _bold(10)
        # Use notes (if any) as the project description fallback; otherwise
        # placeholder. Real source uses dedicated cells but we collapse.
        if doc.notes and doc.notes.strip():
            ws.cell(row=13, column=3, value=doc.notes.split("\n")[0]).font = _normal(9)

        # ---- 5. Issue date (row 17) ----
        date_str = doc.issue_date.strftime("%d/%m/%Y")
        ws.cell(row=17, column=2, value=f"Date : {date_str}").font = _normal(9)

        # ---- 6. Items header (row 23) ----
        items_row = 23
        item_headers = [
            (2, "Libellé"),
            (5, "Qté"),
            (6, "PU HT (€)"),
            (8, "Montant HT (€)"),
            (9, "TVA"),
        ]
        for col, label in item_headers:
            c = ws.cell(row=items_row, column=col, value=label)
            c.font = _bold(10)
            c.fill = _header_fill()
            c.alignment = _center()
            c.border = _thin_border()

        # ---- 7. Items + section headers (row 24+) ----
        row = items_row + 1
        last_category: Optional[str] = None
        for item in doc.items:
            # Section header row
            if item.category and item.category != last_category:
                ws.cell(row=row, column=2, value=item.category).font = _bold(10)
                ws.cell(row=row, column=2).fill = _section_fill()
                ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
                row += 1
                last_category = item.category
            elif not item.category:
                last_category = None

            # Line item row
            ws.cell(row=row, column=2, value=item.description).font = _normal(9)
            ws.cell(row=row, column=2).alignment = _left()
            ws.cell(row=row, column=5, value=float(item.quantity)).font = _normal(9)
            ws.cell(row=row, column=5).alignment = _right()
            ws.cell(row=row, column=6, value=float(item.unit_price)).font = _normal(9)
            ws.cell(row=row, column=6).alignment = _right()
            ws.cell(row=row, column=6).number_format = "#,##0.00"
            ws.cell(row=row, column=8, value=float(item.total_ht)).font = _normal(9)
            ws.cell(row=row, column=8).alignment = _right()
            ws.cell(row=row, column=8).number_format = "#,##0.00"
            ws.cell(row=row, column=9, value=f"{item.vat_rate.normalize()} %").font = _normal(9)
            ws.cell(row=row, column=9).alignment = _right()
            row += 1

        # ---- 8. Totals (row + 1, +2, +3) ----
        row += 1
        ws.cell(row=row, column=6, value="Total HT").font = _bold(10)
        ws.cell(row=row, column=6).alignment = _right()
        ws.cell(row=row, column=8, value=format_eur_fr(doc.total_ht)).font = _bold(10)
        ws.cell(row=row, column=8).alignment = _right()
        row += 1
        ws.cell(row=row, column=6, value="TVA").font = _bold(10)
        ws.cell(row=row, column=6).alignment = _right()
        ws.cell(row=row, column=8, value=format_eur_fr(doc.total_tva)).font = _bold(10)
        ws.cell(row=row, column=8).alignment = _right()
        row += 1
        ws.cell(row=row, column=6, value="Total TTC").font = _bold(11)
        ws.cell(row=row, column=6).alignment = _right()
        ws.cell(row=row, column=8, value=format_eur_fr(doc.total_ttc)).font = _bold(11)
        ws.cell(row=row, column=8).alignment = _right()

        # ---- 9. Bank coordinates ----
        if doc.issuer_iban or doc.issuer_bic:
            row += 3
            ws.cell(row=row, column=2, value="COORDONNÉES BANCAIRES").font = _bold(10)
            ws.cell(row=row, column=2).fill = _section_fill()
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
            if doc.issuer_iban:
                row += 1
                ws.cell(row=row, column=2, value="IBAN").font = _bold(9)
                ws.cell(row=row, column=3, value=doc.issuer_iban).font = _normal(9)
            if doc.issuer_bic:
                row += 1
                ws.cell(row=row, column=2, value="BIC").font = _bold(9)
                ws.cell(row=row, column=3, value=doc.issuer_bic).font = _normal(9)

        # ---- 10. Late-payment legal note (factures only) ----
        if doc.kind == BillingDocumentKind.FACTURE:
            row += 3
            note = (
                "Indemnité forfaitaire de retard de paiement : 40 € "
                "(conformément à l'article 121-II de la loi n° 2012-387 du 22 Mars 2012 "
                "et au décret n° 2012-1115 du 2 Oct. 2012)."
            )
            ws.cell(row=row, column=2, value=note).font = _grey(8)
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=9)
            ws.cell(row=row, column=2).alignment = Alignment(wrap_text=True, vertical="top")

        # Serialise
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()
