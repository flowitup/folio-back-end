"""openpyxl renderer for BillingDocument entities.

Mirrors the source ANN ECO CONSTRUCTION xlsx layout exactly:

  Column grid (A through L, 12 cols):
    A=2.29, B=1.71, C=15.71, D=3.42, E=15.29, F=9.71,
    G=5.14, H=5.29, I=9.29, J=8.42, K=11.29, L=4.57
    (Description spans C..F via merged cells.)

  Row layout (the rows given are the minimum: a block that needs more rows —
  a longer issuer or recipient address, dates and terms under the date —
  pushes everything below it down):
    row 1     A1: legal_name   — bold sz14 cyan (FF00A0DF), centered
    row 2-5   issuer header band (Siège: one address line per row, SIRET, TVA)
    row 8     B="Réf Facture/Devis", D=document_number, H="À l'attention de :"
    rows 9+   right column I = recipient name + every address line + email + SIRET
    row 12 B  "Objet/Opération"           ─┐ only when the document is linked
    rows 13-14 C = project name / address ─┘ to a project
    row 17 B  "<City>, DD/MM/YYYY", then "Valide jusqu'au" (devis) or
              "Échéance" + "Conditions" (facture) on the rows below
    row 19 B  "Madame, Monsieur,"
    rows 20-21 C = standard greeting paragraphs (wrap)
    row 23    items header — ORANGE BG (FFF18728), bold, centered, borders
              B=Libellé G=U H=Qté I=PU(HT)en€ J=Avancement K=Montant(HT)en€ L=TVA
    row 24    project name repeat row (bold dark grey, full-width-ish), if any
    rows 25+  section header rows (col C, bold, centered) interleaved with
              line-item rows (description merged C..F, plus G/H/I/J/K/L cells)
    row N+1   H="Total (HT)" K=SUM K-cells L="€"
    rows N+2… H="TVA <rate> %" K=VAT at that rate L="€" — one row per rate
    then      H="Total (TTC)" K=K_HT+SUM(K_TVA) L="€"
    then      Notes, Conditions générales (one row per line), signature box (H..L)
    then      B="Veuillez agréer, …" (full-width)
    +2        B="COORDONNÉES BANCAIRES" — ORANGE BG, merged B..G, bold
              B="IBAN" / D=value, B="BIC" / D=value
    +2        A=Late-payment legal note — small font (sz=7), merged A..L

Returns raw bytes of an .xlsx file (Open Office XML).
"""

from __future__ import annotations

import math
from io import BytesIO
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.domain.billing.sections import section_headings
from app.domain.billing.document import BillingDocument
from app.domain.billing.document_wording import intro_sentence, place_of_issue
from app.domain.billing.enums import BillingDocumentKind
from app.domain.entities.project import Project
from app.domain.labor.export.format import format_decimal_fr


# ---------------------------------------------------------------------------
# Style helpers / constants
# ---------------------------------------------------------------------------

# Source-observed colors (ANN ECO CONSTRUCTION palette)
COLOR_TITLE_CYAN = "FF00A0DF"
COLOR_ORANGE_BG = "FFF18728"
COLOR_DARK_GREY = "FF222222"

# Column widths (Excel character units) — copied verbatim from the source.
COL_WIDTHS = {
    "A": 2.29,
    "B": 1.71,
    "C": 15.71,
    "D": 3.42,
    "E": 15.29,
    "F": 9.71,
    "G": 5.14,
    "H": 5.29,
    "I": 9.29,
    "J": 8.42,
    "K": 11.29,
    "L": 4.57,
}


def _font(size: int = 11, bold: bool = False, color: Optional[str] = None) -> Font:
    return Font(name="Calibri", size=size, bold=bold, color=color)


def _fill(rgb: str) -> PatternFill:
    return PatternFill(fill_type="solid", fgColor=rgb)


def _thin_box() -> Border:
    s = Side(style="thin", color="FF000000")
    return Border(left=s, right=s, top=s, bottom=s)


def _align(h: str = "general", v: str = "center", wrap: bool = False) -> Alignment:
    return Alignment(horizontal=h, vertical=v, wrap_text=wrap)


# Widths (character units) of the merged ranges free text is written into.
_WIDTH_B_TO_H = sum(COL_WIDTHS[c] for c in "BCDEFGH")
_WIDTH_C_TO_L = sum(COL_WIDTHS[c] for c in "CDEFGHIJKL")
_WIDTH_H_TO_L = sum(COL_WIDTHS[c] for c in "HIJKL")
_ROW_HEIGHT = 15  # points, Excel's default for Calibri 11


def _wrapped_lines(text: str, width: float) -> int:
    """How many lines *text* takes once wrapped in a cell *width* characters wide."""
    return sum(max(1, math.ceil(len(line) / max(width - 1, 1))) for line in text.splitlines() or [""])


def _fit_row_height(ws, row: int, text: str, width: float) -> None:
    """Excel never grows a merged row to fit wrapped text, so size it from the text."""
    lines = _wrapped_lines(text, width)
    if lines > 1:
        ws.row_dimensions[row].height = _ROW_HEIGHT * lines


def _text_cell(ws, row: int, column: int, value: str):
    """Write user text as text: openpyxl stores a string starting with '=' as a formula."""
    c = ws.cell(row=row, column=column, value=value)
    if isinstance(value, str) and value.startswith("="):
        c.data_type = "s"
    return c


def _write_text_block(ws, row: int, title: str, text: str) -> int:
    """Bold *title* in B, then one row per line of *text* (merged C..L). Returns the next free row."""
    ws.cell(row=row, column=2, value=title).font = _font(11, bold=True)
    ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=12)
    row += 1
    for line in text.splitlines():
        if line.strip():
            _text_cell(ws, row, 3, line).font = _font(11)
            ws.cell(row=row, column=3).alignment = _align("left", "top", wrap=True)
            _fit_row_height(ws, row, line, _WIDTH_C_TO_L)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=12)
        row += 1
    return row + 1


def _write_signature_block(ws, row: int, text: str) -> int:
    """Signature block: B..G left blank, a bordered box on H..L holding *text* with room to sign.

    Returns the next free row.
    """
    height = max(4, _wrapped_lines(text, _WIDTH_H_TO_L) + 3)
    last = row + height - 1
    c = _text_cell(ws, row, 8, text)
    c.font = _font(11)
    c.alignment = _align("left", "top", wrap=True)
    c.border = _thin_box()  # merge_cells draws the anchor's sides along the edges of the range
    ws.merge_cells(start_row=row, start_column=8, end_row=last, end_column=12)
    return last + 2


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class OpenpyxlBillingDocumentXlsxRenderer:
    """Renders BillingDocument to xlsx bytes that visually match the
    ANN ECO CONSTRUCTION source format.

    Stateless. Instantiate once and call render() per document.
    """

    def render(self, doc: BillingDocument, project: Optional[Project] = None) -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Devis" if doc.kind == BillingDocumentKind.DEVIS else "Facture"
        ws.sheet_view.showGridLines = False

        # Column widths (verbatim from source)
        for letter, width in COL_WIDTHS.items():
            ws.column_dimensions[letter].width = width

        kind_fr = "Devis" if doc.kind == BillingDocumentKind.DEVIS else "Facture"

        # ---- 1. Issuer header band (rows 1-5) ------------------------------
        # A1: legal name — full-width merged, bold sz14 cyan, centered.
        ws.cell(row=1, column=1, value=doc.issuer_legal_name)
        ws.cell(row=1, column=1).font = _font(14, bold=True, color=COLOR_TITLE_CYAN)
        ws.cell(row=1, column=1).alignment = _align("center", "center")
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=12)

        # Rows 2-3: address, one line per row — merged C..L for room.
        address_lines = [ln.strip() for ln in (doc.issuer_address or "").splitlines() if ln.strip()] or [""]
        for r, line in enumerate(address_lines, start=2):
            ws.cell(row=r, column=3, value=f"Siège : {line}" if r == 2 else line)
            ws.cell(row=r, column=3).font = _font(11)
            ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=12)

        # Row 4: SIRET on left (C..H), row 5: TVA on right (J..L) — lower when the address is longer.
        siret_row = max(4, 2 + len(address_lines))
        if doc.issuer_siret:
            ws.cell(row=siret_row, column=3, value=f"SIRET {doc.issuer_siret}")
            ws.cell(row=siret_row, column=3).font = _font(11)
            ws.merge_cells(start_row=siret_row, start_column=3, end_row=siret_row, end_column=8)

        if doc.issuer_tva_number:
            ws.cell(row=siret_row + 1, column=10, value=f"TVA  {doc.issuer_tva_number}")
            ws.cell(row=siret_row + 1, column=10).font = _font(11)
            ws.merge_cells(start_row=siret_row + 1, start_column=10, end_row=siret_row + 1, end_column=12)
        # Rows the header grew by: every row below moves down with it.
        top = siret_row - 4

        # ---- 2. Doc number row (row 8) -------------------------------------
        ref_row = 8 + top
        ws.cell(row=ref_row, column=2, value=f"Réf {kind_fr}").font = _font(11, bold=True)
        ws.cell(row=ref_row, column=4, value=doc.document_number).font = _font(11)
        ws.cell(row=ref_row, column=8, value="À l'attention de :").alignment = _align("left")
        ws.cell(row=ref_row, column=8).font = _font(11)

        # ---- 3. Recipient block (rows 9+, col I merged I..L) ---------------
        # Name, every address line, email and SIRET — as many rows as it takes.
        recipient_lines = [(doc.recipient_name, True)]
        recipient_lines += [(ln, False) for ln in (doc.recipient_address or "").splitlines() if ln.strip()]
        if doc.recipient_email:
            recipient_lines.append((doc.recipient_email, False))
        if doc.recipient_siret:
            recipient_lines.append((f"SIRET : {doc.recipient_siret}", False))
        for r, (value, bold) in enumerate(recipient_lines, start=ref_row + 1):
            _text_cell(ws, r, 9, value).font = _font(11, bold=bold)
            ws.cell(row=r, column=9).alignment = _align("left", "bottom")
            ws.merge_cells(start_row=r, start_column=9, end_row=r, end_column=12)
        recipient_last_row = ref_row + len(recipient_lines)

        # ---- 4. "Objet / Opération" (row 12 B + rows 13-14 C) ---------------
        # The project the document is linked to: its name, then its address.
        project_title = project.name.strip() if project is not None and project.name else ""
        address = project.address if project is not None and project.address else ""
        project_addr = ", ".join(ln.strip() for ln in address.splitlines() if ln.strip())
        objet_row = 12 + top
        if project_title:
            ws.cell(row=objet_row, column=2, value="Objet/Opération").font = _font(11, bold=True)
            _text_cell(ws, objet_row + 1, 3, project_title).font = _font(11)
            ws.merge_cells(start_row=objet_row + 1, start_column=3, end_row=objet_row + 1, end_column=8)
            if project_addr and project_addr != project_title:
                _text_cell(ws, objet_row + 2, 3, project_addr).font = _font(11)
                ws.merge_cells(start_row=objet_row + 2, start_column=3, end_row=objet_row + 2, end_column=8)

        # ---- 5. Issue date (row 17 B) + validity / due date / terms -------
        # Format: "<City>, DD/MM/YYYY" — the city is read from the free-text address.
        date_row = 17 + top
        city = place_of_issue(doc.issuer_address)
        date_str = doc.issue_date.strftime("%d/%m/%Y")
        line = f"{city}, {date_str}" if city else date_str
        ws.cell(row=date_row, column=2, value=line).font = _font(11)
        ws.merge_cells(start_row=date_row, start_column=2, end_row=date_row, end_column=8)
        meta_lines = []
        if doc.kind == BillingDocumentKind.DEVIS and doc.validity_until:
            meta_lines.append(f"Valide jusqu'au : {doc.validity_until.strftime('%d/%m/%Y')}")
        if doc.kind == BillingDocumentKind.FACTURE:
            if doc.payment_due_date:
                meta_lines.append(f"Échéance : {doc.payment_due_date.strftime('%d/%m/%Y')}")
            if doc.payment_terms:
                meta_lines.append(f"Conditions : {doc.payment_terms}")
        for r, text in enumerate(meta_lines, start=date_row + 1):
            _text_cell(ws, r, 2, text).font = _font(11)
            ws.cell(row=r, column=2).alignment = _align("left", "top", wrap=True)
            ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
            _fit_row_height(ws, r, text, _WIDTH_B_TO_H)

        # ---- 6. Greeting (rows 19-21) --------------------------------------
        greeting_row = max(19 + top, date_row + len(meta_lines) + 2, recipient_last_row + 2)
        ws.cell(row=greeting_row, column=2, value="Madame, Monsieur,").font = _font(11)
        ws.cell(row=greeting_row + 1, column=3, value=intro_sentence(doc.kind)).font = _font(11)
        ws.cell(row=greeting_row + 1, column=3).alignment = _align(wrap=True)
        ws.merge_cells(start_row=greeting_row + 1, start_column=3, end_row=greeting_row + 1, end_column=12)
        ws.cell(
            row=greeting_row + 2,
            column=3,
            value="Je reste à votre disposition pour toute précision ou complément d'information.",
        ).font = _font(11)
        ws.cell(row=greeting_row + 2, column=3).alignment = _align(wrap=True)
        ws.merge_cells(start_row=greeting_row + 2, start_column=3, end_row=greeting_row + 2, end_column=12)
        ws.cell(row=greeting_row, column=2).font = _font(11)
        ws.merge_cells(start_row=greeting_row, start_column=2, end_row=greeting_row, end_column=12)

        # ---- 7. Items header (row 23) — ORANGE bg, bold, centered, borders -
        items_header_row = greeting_row + 4
        header_cells = [
            (2, "Libellé"),
            (7, "U"),
            (8, "Qté"),
            (9, "PU\n (HT) en €"),
            (10, "Avancement"),
            (11, "Montant (HT) en €"),
            (12, "TVA"),
        ]
        for col, label in header_cells:
            c = ws.cell(row=items_header_row, column=col, value=label)
            c.font = _font(11, bold=True)
            c.fill = _fill(COLOR_ORANGE_BG)
            c.alignment = _align("center", "center", wrap=True)
            c.border = _thin_box()
        # Description label spans B..F
        ws.merge_cells(start_row=items_header_row, start_column=2, end_row=items_header_row, end_column=6)

        # ---- 8. Project repeat row (row 24) — bold dark grey, only with a project
        row = items_header_row + 1
        if project_title:
            c = _text_cell(ws, row, 2, project_title)
            c.font = _font(11, bold=True, color=COLOR_DARK_GREY)
            c.alignment = _align("left", "center", wrap=True)
            c.border = _thin_box()
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
            # Empty bordered cells for G-L
            for col in range(7, 13):
                ws.cell(row=row, column=col).border = _thin_box()
            row += 1

        # ---- 9. Items + section headers (row 25+) --------------------------
        first_item_row = None
        last_item_row = None
        headings = section_headings(item.category for item in doc.items)
        for item, heading in zip(doc.items, headings):
            # Insert section header row when the section changes
            if heading is not None:
                sh = ws.cell(row=row, column=3, value=heading)
                sh.font = _font(11, bold=True)
                sh.alignment = _align("center", "center", wrap=True)
                sh.border = _thin_box()
                ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
                # Border the rest of the row (B + G..L) for visual continuity
                for col in [2] + list(range(7, 13)):
                    ws.cell(row=row, column=col).border = _thin_box()
                row += 1

            # Item row
            if first_item_row is None:
                first_item_row = row
            last_item_row = row
            # Description (merged C..F)
            d = _text_cell(ws, row, 3, item.description)
            d.font = _font(11)
            d.alignment = _align("left", "center", wrap=True)
            d.border = _thin_box()
            ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
            # Empty B (left margin) cell with border
            ws.cell(row=row, column=2).border = _thin_box()
            # G = unit (we don't model it — leave blank but bordered)
            ws.cell(row=row, column=7).border = _thin_box()
            ws.cell(row=row, column=7).alignment = _align("center", "center")
            # H = Qté
            ws.cell(row=row, column=8, value=float(item.quantity)).font = _font(11)
            ws.cell(row=row, column=8).alignment = _align("center", "center")
            ws.cell(row=row, column=8).border = _thin_box()
            # I = PU HT
            ws.cell(row=row, column=9, value=float(item.unit_price)).font = _font(11)
            ws.cell(row=row, column=9).alignment = _align("center", "center")
            # Shows a price's own decimals (15.015, not 15.02) so Qté × PU gives the line amount.
            ws.cell(row=row, column=9).number_format = "#,##0.00####"
            ws.cell(row=row, column=9).border = _thin_box()
            # J = Avancement (default 100% — we don't model partial advancement)
            ws.cell(row=row, column=10, value=1).font = _font(11)
            ws.cell(row=row, column=10).alignment = _align("center", "center")
            ws.cell(row=row, column=10).number_format = "0.00%"
            ws.cell(row=row, column=10).border = _thin_box()
            # K = Montant HT — formula so Excel recomputes if user edits; rounded to the
            # cent like the app's line totals, so the SUM matches the document's HT.
            ws.cell(row=row, column=11, value=f"=ROUND(I{row}*J{row}*H{row},2)").font = _font(11)
            ws.cell(row=row, column=11).alignment = _align("center", "center")
            ws.cell(row=row, column=11).number_format = "#,##0.00"
            ws.cell(row=row, column=11).border = _thin_box()
            # L = TVA (decimal: 0.1, 0.2)
            ws.cell(row=row, column=12, value=float(item.vat_rate) / 100).font = _font(11)
            ws.cell(row=row, column=12).alignment = _align("center", "center")
            ws.cell(row=row, column=12).number_format = "0.00%"
            ws.cell(row=row, column=12).border = _thin_box()
            row += 1

        # ---- 10. Totals (rows N+1 .. N+3) ---------------------------------
        if last_item_row is None:
            # No items — bail out before totals/footer to keep file valid.
            buf = BytesIO()
            wb.save(buf)
            return buf.getvalue()

        totals_row_ht = row
        vat_rows = doc.vat_breakdown
        first_tva_row = totals_row_ht + 1
        last_tva_row = totals_row_ht + len(vat_rows)
        totals_row_ttc = last_tva_row + 1

        # Total HT
        ws.cell(row=totals_row_ht, column=8, value="Total (HT)").font = _font(11, bold=True)
        ws.cell(row=totals_row_ht, column=8).alignment = _align("center", "center")
        ws.cell(
            row=totals_row_ht,
            column=11,
            value=f"=SUM(K{first_item_row}:K{last_item_row})",
        ).font = _font(11, bold=True)
        ws.cell(row=totals_row_ht, column=11).number_format = "#,##0.00"
        ws.cell(row=totals_row_ht, column=12, value="€").font = _font(11, bold=True)
        ws.cell(row=totals_row_ht, column=12).alignment = _align("center", "center")

        # TVA — one row per rate, from the doc's computed breakdown (the same one the PDF
        # prints) rather than =K_HT*rate, so mixed-rate documents add up exactly.
        for r, (rate, _base_ht, tva_amt) in enumerate(vat_rows, start=first_tva_row):
            ws.cell(row=r, column=8, value=f"TVA {format_decimal_fr(rate)}\u00a0%").font = _font(11, bold=True)
            ws.cell(row=r, column=8).alignment = _align("center", "center")
            ws.cell(row=r, column=11, value=float(tva_amt)).font = _font(11, bold=True)
            ws.cell(row=r, column=11).number_format = "#,##0.00"
            ws.cell(row=r, column=12, value="€").font = _font(11, bold=True)
            ws.cell(row=r, column=12).alignment = _align("center", "center")

        # Total TTC
        ws.cell(row=totals_row_ttc, column=8, value="Total (TTC)").font = _font(11, bold=True)
        ws.cell(row=totals_row_ttc, column=8).alignment = _align("center", "center")
        ws.cell(
            row=totals_row_ttc,
            column=11,
            value=f"=K{totals_row_ht}+SUM(K{first_tva_row}:K{last_tva_row})",
        ).font = _font(11, bold=True)
        ws.cell(row=totals_row_ttc, column=11).number_format = "#,##0.00"
        ws.cell(row=totals_row_ttc, column=12, value="€").font = _font(11, bold=True)
        ws.cell(row=totals_row_ttc, column=12).alignment = _align("center", "center")

        # ---- 11. Notes, conditions générales, signature block --------------
        row = totals_row_ttc + 2
        if doc.notes and doc.notes.strip():
            row = _write_text_block(ws, row, "Notes", doc.notes)
        if doc.terms and doc.terms.strip():
            row = _write_text_block(ws, row, "Conditions générales", doc.terms)
        if doc.signature_block_text and doc.signature_block_text.strip():
            row = _write_signature_block(ws, row, doc.signature_block_text)

        # ---- 12. Closing greeting -------------------------------------------
        closing_row = row
        ws.cell(
            row=closing_row,
            column=2,
            value="Veuillez agréer, Madame, Monsieur, l'expression de nos salutations distinguées.",
        ).font = _font(11)
        ws.merge_cells(start_row=closing_row, start_column=2, end_row=closing_row, end_column=12)

        # ---- 13. Bank coords block -----------------------------------------
        if doc.issuer_iban or doc.issuer_bic:
            bank_title_row = closing_row + 2
            c = ws.cell(row=bank_title_row, column=2, value="COORDONNÉES BANCAIRES")
            c.font = _font(11, bold=True)
            c.fill = _fill(COLOR_ORANGE_BG)
            c.alignment = _align("center", "center")
            ws.merge_cells(start_row=bank_title_row, start_column=2, end_row=bank_title_row, end_column=7)
            offset = 1
            if doc.issuer_iban:
                r = bank_title_row + offset
                ws.cell(row=r, column=2, value="IBAN").font = _font(11)
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
                ws.cell(row=r, column=4, value=doc.issuer_iban).font = _font(11)
                ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=12)
                offset += 1
            if doc.issuer_bic:
                r = bank_title_row + offset
                ws.cell(row=r, column=2, value="BIC").font = _font(11)
                ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
                ws.cell(row=r, column=4, value=doc.issuer_bic).font = _font(11)
                ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=12)
                offset += 1
            footer_anchor = bank_title_row + offset + 1
        else:
            footer_anchor = closing_row + 2

        # ---- 14. Late-payment legal note (factures only) ------------------
        if doc.kind == BillingDocumentKind.FACTURE:
            note = (
                "Indemnité forfaitaire de retard de paiement: 40€ "
                "(conformément à l'article 121-II de la loi n° 2012-387 du 22 Mars 2012 "
                "et au décret n° 2012-1115 du 2 Oct. 2012)"
            )
            ws.cell(row=footer_anchor, column=1, value=note).font = _font(7)
            ws.cell(row=footer_anchor, column=1).alignment = _align("left", "top", wrap=True)
            ws.merge_cells(start_row=footer_anchor, start_column=1, end_row=footer_anchor, end_column=12)

        # Serialise
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()
