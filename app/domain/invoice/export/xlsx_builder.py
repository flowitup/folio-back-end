"""Pure-python xlsx builder for invoice export.

build_xlsx(context: InvoiceExportContext, bundle: InvoiceBundle) -> bytes

Sheet layout
------------
Sheet 1 "Summary"   — KPI table, subtotals by type, full invoice list + grand total
Sheets 2..k         — one sheet per InvoiceType that has at least one invoice

Currency rule (LOCKED)
-----------------------
All currency cells carry RAW float values so Excel can sort/sum them, rounded
half-up to the cent first (the amounts the app shows, never 5.5476).
The cell.number_format is set to EUR_FR_FORMAT (imported from labor xlsx_builder —
do NOT redefine it) which renders the value as 200,00 € in fr-FR locales.
Never write pre-formatted strings to currency cells.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import List

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

# Reuse the canonical EUR_FR_FORMAT constant — do not redefine.
from app.domain.labor.export.xlsx_builder import EUR_FR_FORMAT  # noqa: F401

from app.domain.invoice.export.format import (
    format_generated_at,
    invoice_type_label,
    is_funding,
    is_funding_subtotal,
    month_label,
    round_cents,
    subtotal_type_label,
)
from app.domain.invoice.export.labels import t
from app.domain.invoice.export.models import InvoiceBundle, InvoiceExportContext, TypeSubtotal
from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.services.xlsx_safe_cell import safe_cell

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

# The invoice number (not a 1, 2, 3 counter) identifies each row, and the payment
# method says which purse paid it.
_INVOICE_HEADERS = [
    "col_invoice_number",
    "col_date",
    "col_type",
    "col_recipient",
    "col_payment_method",
    "col_items",
    "col_total",
]
_INVOICE_COL_WIDTHS = [18, 12, 24, 30, 20, 8, 16]

_TYPE_SHEET_HEADERS = [
    "col_invoice_number",
    "col_date",
    "col_recipient",
    "col_payment_method",
    "col_items",
    "col_total",
]
_TYPE_SHEET_COL_WIDTHS = [18, 12, 30, 20, 8, 16]

# Labor sheet gets an extra "Payment month" column so payroll periods are visible
# alongside the invoice issue date.
_LABOR_TYPE_SHEET_HEADERS = [
    "col_invoice_number",
    "col_date",
    "col_recipient",
    "col_payment_method",
    "col_items",
    "col_payment_month",
    "col_total",
]
_LABOR_TYPE_SHEET_COL_WIDTHS = [18, 12, 30, 20, 8, 16, 16]

_SUBTYPE_HEADERS = ["col_type", "col_invoice_count", "col_total"]
_SUBTYPE_COL_WIDTHS = [16, 16, 18]

_KPI_COL_WIDTHS = [22, 30]


def _header(locale: str, key: str) -> str:
    """Column header text: "#" stays as is, every other entry is a label key."""
    return key if key == "#" else t(locale, key)


# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------


def _bold_font(size: int = 11) -> Font:
    return Font(bold=True, size=size)


def _italic_font(size: int = 9) -> Font:
    return Font(italic=True, size=size)


def _thin_border() -> Border:
    thin = Side(style="thin")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _thick_top_border() -> Border:
    thick = Side(style="medium")
    return Border(top=thick)


def _header_fill() -> PatternFill:
    return PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")


def _grand_total_fill() -> PatternFill:
    return PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")


def _set_col_widths(ws: Worksheet, widths: List[int]) -> None:
    for i, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(i + 1)].width = w


def _money(value: Decimal) -> float:
    """A currency cell's value: the amount rounded to the cent, as a float Excel can sum."""
    return float(round_cents(value))


def _merge_and_write(ws: Worksheet, row: int, start_col: int, end_col: int, value: str, font: Font) -> None:
    ws.merge_cells(start_row=row, start_column=start_col, end_row=row, end_column=end_col)
    cell = safe_cell(ws, row, start_col, value)
    cell.font = font
    cell.alignment = Alignment(horizontal="left", vertical="center")


# ---------------------------------------------------------------------------
# Invoice sort key
# ---------------------------------------------------------------------------


def _invoice_sort_key(inv: Invoice):
    return (inv.issue_date, inv.ledger_type.value, inv.invoice_number)


# ---------------------------------------------------------------------------
# Summary sheet helpers
# ---------------------------------------------------------------------------


def _write_summary_header_band(ws: Worksheet, context: InvoiceExportContext) -> int:
    """Row 1: title band. Row 2: meta line. Row 3: blank. Returns next_row (4)."""
    from_label = month_label(context.range.from_month, context.locale)
    to_label = month_label(context.range.to_month, context.locale)

    # Row 1 — title
    _merge_and_write(
        ws,
        row=1,
        start_col=1,
        end_col=6,
        value=f"{t(context.locale, 'export_title')} — {context.project_name}",
        font=Font(bold=True, size=14),
    )

    # Row 2 — meta
    meta = (
        t(context.locale, "range", start=from_label, end=to_label)
        + " · "
        + t(
            context.locale,
            "generated",
            at=format_generated_at(context.generated_at),
            email=context.generated_by_email,
        )
    )
    cell2 = safe_cell(ws, 2, 1, meta)
    cell2.font = Font(italic=True, size=9)

    # Row 3 blank
    return 4


def _write_kpi_table(ws: Worksheet, start_row: int, context: InvoiceExportContext, bundle: InvoiceBundle) -> int:
    """Write 2-col KPI table. Returns next_row after blank spacer."""
    from_label = month_label(context.range.from_month, context.locale)
    to_label = month_label(context.range.to_month, context.locale)

    kpi_rows = [
        (t(context.locale, "total_invoices"), bundle.invoice_count),
        (t(context.locale, "total_expenses"), bundle.grand_total),
        (t(context.locale, "released_funds"), bundle.released_total),
        (t(context.locale, "from_month"), from_label),
        (t(context.locale, "to_month"), to_label),
    ]

    thin = _thin_border()
    fill = _header_fill()

    for i, (label, value) in enumerate(kpi_rows):
        r = start_row + i
        label_cell = safe_cell(ws, r, 1, label)
        label_cell.font = _bold_font(10)
        label_cell.fill = fill
        label_cell.border = thin

        if isinstance(value, Decimal):  # money rows — write float once, apply currency format
            val_cell = safe_cell(ws, r, 2, _money(value))
            val_cell.number_format = EUR_FR_FORMAT
        else:
            val_cell = safe_cell(ws, r, 2, value)
            val_cell.alignment = Alignment(horizontal="left")
        val_cell.border = thin

    ws.column_dimensions["A"].width = _KPI_COL_WIDTHS[0]
    ws.column_dimensions["B"].width = _KPI_COL_WIDTHS[1]

    return start_row + len(kpi_rows) + 1  # data rows + 1 blank


def _write_subtotals_section(ws: Worksheet, start_row: int, subtotals: List[TypeSubtotal], locale: str) -> int:
    """Write 'Subtotals by type' heading + header row + data rows. Returns next_row."""
    thin = _thin_border()
    fill = _header_fill()

    # Heading
    heading_cell = safe_cell(ws, start_row, 1, t(locale, "subtotals_by_type"))
    heading_cell.font = _bold_font(11)

    # Header row
    hdr_row = start_row + 1
    for i, label in enumerate(_SUBTYPE_HEADERS):
        cell = safe_cell(ws, hdr_row, i + 1, t(locale, label))
        cell.font = _bold_font()
        cell.border = thin
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")

    # Data rows: the expenses first, right above the total they add up to (in the
    # KPI table and the invoices section), then the funding rows marked as left out.
    ordered = [s for s in subtotals if not is_funding_subtotal(s)] + [s for s in subtotals if is_funding_subtotal(s)]
    data_row = hdr_row + 1
    for sub in ordered:
        cells_data = [subtotal_type_label(sub, locale), sub.invoice_count, sub.total_amount]
        for col_idx, val in enumerate(cells_data):
            cell = safe_cell(ws, data_row, col_idx + 1, val)
            cell.border = thin
            if col_idx == 2:  # Total column — currency
                cell.value = _money(sub.total_amount)
                cell.number_format = EUR_FR_FORMAT
                cell.alignment = Alignment(horizontal="right")
            elif col_idx == 1:
                cell.alignment = Alignment(horizontal="center")
        data_row += 1

    return data_row + 1  # blank spacer


def _write_invoice_rows(ws: Worksheet, first_row: int, invoices: List[Invoice], locale: str) -> int:
    """Write one row per invoice from `first_row`. Returns the next free row."""
    thin = _thin_border()
    total_col = len(_INVOICE_HEADERS) - 1
    data_row = first_row
    for inv in sorted(invoices, key=_invoice_sort_key):
        values = [
            inv.invoice_number,
            inv.issue_date,
            invoice_type_label(inv, locale),
            inv.recipient_name,
            inv.payment_method_label or "",
            len(inv.items),
            _money(inv.total_amount),
        ]
        for col_idx, val in enumerate(values):
            cell = safe_cell(ws, data_row, col_idx + 1, val)
            cell.border = thin
            if col_idx == total_col:  # Total column — currency
                cell.number_format = EUR_FR_FORMAT
                cell.alignment = Alignment(horizontal="right")
            elif col_idx == total_col - 1:
                cell.alignment = Alignment(horizontal="center")
            elif col_idx == 1:
                cell.number_format = "YYYY-MM-DD"
        data_row += 1
    return data_row


def _write_total_band(ws: Worksheet, row: int, label: str, amount: Decimal) -> None:
    """A bold total row with a thick top border: label in column A, amount in the Total column."""
    thick = _thick_top_border()
    grand_fill = _grand_total_fill()
    for col in range(1, len(_INVOICE_HEADERS) + 1):
        cell = ws.cell(row=row, column=col)
        cell.border = thick
        cell.fill = grand_fill
        cell.font = _bold_font()
    safe_cell(ws, row, 1, label).font = _bold_font()
    total_cell = ws.cell(row=row, column=len(_INVOICE_HEADERS), value=_money(amount))
    total_cell.number_format = EUR_FR_FORMAT
    total_cell.font = _bold_font()
    total_cell.border = thick
    total_cell.fill = grand_fill
    total_cell.alignment = Alignment(horizontal="right")


def _write_invoices_section(ws: Worksheet, start_row: int, bundle: InvoiceBundle, locale: str) -> int:
    """'Invoices' heading + header + the expense rows and their total, then the
    funding rows (released funds, cash advances) in a section of their own with
    their totals, so each total is the sum of the rows right above it. Returns next_row.
    """
    thin = _thin_border()
    fill = _header_fill()

    expenses = [inv for inv in bundle.invoices if not is_funding(inv)]
    funding = [inv for inv in bundle.invoices if is_funding(inv)]

    def header(row: int) -> None:
        for i, label in enumerate(_INVOICE_HEADERS):
            cell = safe_cell(ws, row, i + 1, _header(locale, label))
            cell.font = _bold_font()
            cell.border = thin
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center")

    row = start_row
    if expenses:
        safe_cell(ws, row, 1, t(locale, "invoices")).font = _bold_font(11)
        header(row + 1)
        row = _write_invoice_rows(ws, row + 2, expenses, locale)
        # The same total as the KPI table: the expense rows above, released funds left out.
        _write_total_band(ws, row, t(locale, "grand_total_row"), bundle.grand_total)
        row += 2

    if funding:
        safe_cell(ws, row, 1, t(locale, "financing")).font = _bold_font(11)
        header(row + 1)
        row = _write_invoice_rows(ws, row + 2, funding, locale)
        released = [inv for inv in funding if not inv.is_cash_advance]
        advances = [inv for inv in funding if inv.is_cash_advance]
        if released:
            _write_total_band(ws, row, t(locale, "released_total_row"), bundle.released_total)
            row += 1
        if advances:
            advance_total = sum((round_cents(inv.total_amount) for inv in advances), Decimal("0"))
            _write_total_band(ws, row, t(locale, "cash_advance_total_row"), advance_total)
            row += 1

    return row + 1


# ---------------------------------------------------------------------------
# Per-type sheet writer
# ---------------------------------------------------------------------------


def _write_type_sheet(
    ws: Worksheet, context: InvoiceExportContext, invoice_type: InvoiceType, type_name: str, invoices: List[Invoice]
) -> None:
    """Write header band + invoice table + footer total for a single type sheet.

    The labor sheet carries an extra "Payment month" column (service_month) so
    payroll periods are visible alongside the invoice issue date.
    """
    thin = _thin_border()
    thick = _thick_top_border()
    fill = _header_fill()

    is_labor = invoice_type == InvoiceType.LABOR
    headers = _LABOR_TYPE_SHEET_HEADERS if is_labor else _TYPE_SHEET_HEADERS
    col_widths = _LABOR_TYPE_SHEET_COL_WIDTHS if is_labor else _TYPE_SHEET_COL_WIDTHS
    total_col_idx = len(headers) - 1  # 0-based index of the Total column

    from_label = month_label(context.range.from_month, context.locale)
    to_label = month_label(context.range.to_month, context.locale)

    # Row 1 — title
    _merge_and_write(
        ws,
        row=1,
        start_col=1,
        end_col=5,
        value=t(context.locale, "type_title", type=type_name, project=context.project_name),
        font=Font(bold=True, size=13),
    )

    # Row 2 — meta
    meta = t(context.locale, "range", start=from_label, end=to_label)
    safe_cell(ws, 2, 1, meta).font = _italic_font(9)

    # Row 3 blank → header at row 4
    hdr_row = 4
    for i, label in enumerate(headers):
        cell = safe_cell(ws, hdr_row, i + 1, _header(context.locale, label))
        cell.font = _bold_font()
        cell.border = thin
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")

    # Data rows
    sorted_invoices = sorted(invoices, key=_invoice_sort_key)
    data_row = hdr_row + 1
    for inv in sorted_invoices:
        item_count = len(inv.items)
        values = [inv.invoice_number, inv.issue_date, inv.recipient_name, inv.payment_method_label or "", item_count]
        if is_labor:
            values.append(inv.service_month)
        values.append(_money(inv.total_amount))
        for col_idx, val in enumerate(values):
            cell = safe_cell(ws, data_row, col_idx + 1, val)
            cell.border = thin
            if col_idx == total_col_idx:  # Total
                cell.number_format = EUR_FR_FORMAT
                cell.alignment = Alignment(horizontal="right")
            elif col_idx == 4:
                cell.alignment = Alignment(horizontal="center")
            elif col_idx == 1:
                cell.number_format = "YYYY-MM-DD"
            elif is_labor and col_idx == 5:
                # Payment month is month-granular; render as MM/YYYY to match the PDF export.
                cell.number_format = "MM/YYYY"
        data_row += 1

    # Footer total row
    footer_row = data_row
    type_total = sum((round_cents(inv.total_amount) for inv in invoices), Decimal("0"))
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=footer_row, column=col)
        cell.border = thick
        cell.font = _bold_font()

    safe_cell(ws, footer_row, 1, t(context.locale, "total_row")).font = _bold_font()
    ws.cell(row=footer_row, column=1).border = thick

    total_cell = ws.cell(row=footer_row, column=len(headers), value=_money(type_total))
    total_cell.number_format = EUR_FR_FORMAT
    total_cell.font = _bold_font()
    total_cell.border = thick
    total_cell.alignment = Alignment(horizontal="right")

    _set_col_widths(ws, col_widths)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_xlsx(context: InvoiceExportContext, bundle: InvoiceBundle) -> bytes:
    """Generate an xlsx workbook for invoice export and return its raw bytes.

    Sheet 1 "Summary": header band, KPI table, subtotals by type, invoices table,
    grand total band.

    Sheets 2..k: one sheet per InvoiceType present in the bundle (skip empty types).
    Sheet titles: "Released Funds invoices" / "Labor invoices" / "Materials & Services invoices".

    Empty range: only the Summary sheet, with an italic "No invoices in range" line
    replacing KPI/tables.

    Currency cells store raw float values; number_format renders as fr-FR EUR in Excel.
    """
    wb = openpyxl.Workbook()
    ws_summary = wb.active
    ws_summary.title = t(context.locale, "summary_sheet")

    next_row = _write_summary_header_band(ws_summary, context)

    if bundle.invoice_count == 0:
        from_label = month_label(context.range.from_month, context.locale)
        to_label = month_label(context.range.to_month, context.locale)
        empty_cell = ws_summary.cell(
            row=next_row, column=1, value=t(context.locale, "no_invoices", start=from_label, end=to_label)
        )
        empty_cell.font = Font(italic=True)
        _set_col_widths(ws_summary, _INVOICE_COL_WIDTHS)
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # KPI table
    next_row = _write_kpi_table(ws_summary, start_row=next_row, context=context, bundle=bundle)

    # Subtotals by type
    next_row = _write_subtotals_section(
        ws_summary, start_row=next_row, subtotals=bundle.subtotals_by_type, locale=context.locale
    )

    # Invoices table + grand total
    _write_invoices_section(ws_summary, start_row=next_row, bundle=bundle, locale=context.locale)

    _set_col_widths(ws_summary, _INVOICE_COL_WIDTHS)

    # Per-type sheets — one per type that has invoices (maintain canonical order).
    # OTHERS and RETURN are included so new types never trigger a KeyError. Cash
    # advances get a sheet of their own, as they get a subtotal row of their own:
    # each sheet's total is the matching subtotal.
    for invoice_type, cash_advance in (
        (InvoiceType.RELEASED_FUNDS, False),
        (InvoiceType.LABOR, False),
        (InvoiceType.MATERIALS_SERVICES, False),
        (InvoiceType.OTHERS, False),
        (InvoiceType.OTHERS, True),
        (InvoiceType.RETURN, False),
    ):
        type_invoices = [
            inv for inv in bundle.invoices if inv.ledger_type == invoice_type and inv.is_cash_advance == cash_advance
        ]
        if not type_invoices:
            continue
        type_name = invoice_type_label(type_invoices[0], context.locale)
        # Excel caps sheet titles at 31 characters.
        sheet_title = t(context.locale, "type_sheet", type=type_name)[:31]
        ws_type = wb.create_sheet(title=sheet_title)
        _write_type_sheet(ws_type, context, invoice_type, type_name, type_invoices)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
