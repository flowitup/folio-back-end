"""Pure-python PDF builder for labor export.

build_pdf(context: ExportContext, buckets: list[MonthBucket]) -> bytes

Layout (A4 portrait, 15mm margins)
-----------------------------------
  1. Header block — project name, range, generated_at, generated_by
  2. KPI mini-table — Total cost | Worker-days | Bonus cost | Bonus days | Banked hours
  3. Per-worker breakdown table — aggregated across all months
     Columns: Worker | Days | Banked hrs | Bonus full | Bonus half |
              Priced cost | Bonus cost | Total (priced + bonus)
  4. Page-X footer on every page (Page X only — two-pass X/Y is future work)

Currency rule (LOCKED)
-----------------------
All monetary values formatted via format_eur_fr(Decimal) → "200,00 €"
(matches xlsx number_format + FE Intl.NumberFormat fr-FR).

No daily detail — lives only in xlsx (locked decision).

Empty-range case
-----------------
If all buckets have empty summary.rows:
  header + "No labor entries in range …" paragraph, no KPI table, no breakdown table.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .format import format_eur_fr
from .models import ExportContext, MonthBucket

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FONTS_DIR = Path(__file__).parent / "fonts"
_FONT_REGISTERED = False

# Table column headers (8 columns)
_BREAKDOWN_HEADERS = [
    "Worker",
    "Days",
    "Banked hrs",
    "Bonus full",
    "Bonus half",
    "Priced cost",
    "Bonus cost",
    "Total (priced + bonus)",
]

# Relative column widths for breakdown table (sum normalised to page width in builder)
_BREAKDOWN_COL_WEIGHTS = [4, 1.5, 1.5, 1.5, 1.5, 2, 2, 2.5]

# KPI labels and attribute names for aggregation
_KPI_LABELS = [
    "Total cost",
    "Worker-days",
    "Bonus cost",
    "Bonus days",
    "Banked hours",
]

# Indices of right-aligned (numeric/currency) columns in breakdown table (0-based)
_NUMERIC_COLS = {1, 2, 3, 4, 5, 6, 7}


# ---------------------------------------------------------------------------
# Font registration (run-once, idempotent)
# ---------------------------------------------------------------------------


def _register_fonts() -> None:
    """Register DejaVu Sans + Bold for Vietnamese / French / Latin support."""
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    pdfmetrics.registerFont(TTFont("DejaVu", str(_FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        "DejaVu",
        normal="DejaVu",
        bold="DejaVu-Bold",
        italic="DejaVu",
        boldItalic="DejaVu-Bold",
    )
    _FONT_REGISTERED = True


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------


def _make_styles() -> dict:
    """Return a dict of ParagraphStyle objects keyed by logical name.

    All styles use the DejaVu family — never Helvetica — so Unicode characters
    (Vietnamese diacritics, accented French) render correctly.
    """
    base = getSampleStyleSheet()
    # Suppress unused variable; we build from scratch to guarantee DejaVu
    _ = base

    h1 = ParagraphStyle(
        "h1",
        fontName="DejaVu-Bold",
        fontSize=16,
        leading=20,
        spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "h2",
        fontName="DejaVu",
        fontSize=10,
        leading=14,
        spaceAfter=2,
    )
    body = ParagraphStyle(
        "body",
        fontName="DejaVu",
        fontSize=9,
        leading=12,
    )
    body_italic = ParagraphStyle(
        "body_italic",
        fontName="DejaVu",
        fontSize=14,
        leading=18,
        textColor=colors.grey,
    )
    kpi_label = ParagraphStyle(
        "kpi_label",
        fontName="DejaVu",
        fontSize=7,
        leading=9,
        textColor=colors.grey,
        alignment=1,  # centre
    )
    kpi_value = ParagraphStyle(
        "kpi_value",
        fontName="DejaVu-Bold",
        fontSize=10,
        leading=13,
        alignment=1,  # centre
    )
    footer_style = ParagraphStyle(
        "footer_style",
        fontName="DejaVu",
        fontSize=8,
        leading=10,
    )
    return {
        "h1": h1,
        "h2": h2,
        "body": body,
        "body_italic": body_italic,
        "kpi_label": kpi_label,
        "kpi_value": kpi_value,
        "footer": footer_style,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class _AggRow:
    worker_id: str
    worker_name: str
    days_worked: int
    banked_hours: int
    bonus_full_days: int
    bonus_half_days: int
    priced_cost: float
    bonus_cost: float
    total_cost: float


def _aggregate_across_buckets(buckets: List[MonthBucket]) -> List[_AggRow]:
    """Sum per-worker fields across all buckets.

    Keyed by worker_id. First encounter wins for worker_name.
    Output sorted by worker_name (Unicode-stable).
    """
    agg: dict[str, _AggRow] = {}
    for bucket in buckets:
        for row in bucket.summary.rows:
            priced = float(row.total_cost) - float(row.bonus_cost)
            if row.worker_id not in agg:
                agg[row.worker_id] = _AggRow(
                    worker_id=row.worker_id,
                    worker_name=row.worker_name,
                    days_worked=row.days_worked,
                    banked_hours=row.banked_hours,
                    bonus_full_days=row.bonus_full_days,
                    bonus_half_days=row.bonus_half_days,
                    priced_cost=priced,
                    bonus_cost=float(row.bonus_cost),
                    total_cost=float(row.total_cost),
                )
            else:
                a = agg[row.worker_id]
                a.days_worked += row.days_worked
                a.banked_hours += row.banked_hours
                a.bonus_full_days += row.bonus_full_days
                a.bonus_half_days += row.bonus_half_days
                a.priced_cost += priced
                a.bonus_cost += float(row.bonus_cost)
                a.total_cost += float(row.total_cost)
    return sorted(agg.values(), key=lambda r: r.worker_name)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _render_header(context: ExportContext, styles: dict) -> list:
    """Build header block for the export document.

    Project-wide: 4 paragraphs (title, project, range, generated).
    Single-worker: same 4 + extra worker line with name and daily rate.
    """
    from_label = context.range.from_month.strftime("%b %Y")
    to_label = context.range.to_month.strftime("%b %Y")
    # Count months: inclusive range
    from_dt = context.range.from_month
    to_dt = context.range.to_month
    n_months = (to_dt.year - from_dt.year) * 12 + (to_dt.month - from_dt.month) + 1

    elements = [
        Paragraph("Folio · Labor Export", styles["h1"]),
        Paragraph(f"Project: {context.project_name}", styles["h2"]),
        Paragraph(
            f"Range: {from_label} → {to_label} ({n_months} month{'s' if n_months != 1 else ''})",
            styles["h2"],
        ),
        Paragraph(
            f"Generated: {context.generated_at.strftime('%Y-%m-%d %H:%M UTC')} " f"by {context.generated_by_email}",
            styles["h2"],
        ),
    ]

    if context.worker_name is not None:
        rate = context.worker_daily_rate
        rate_str = format_eur_fr(rate) if rate is not None else "—"
        elements.append(Paragraph(f"Worker: {context.worker_name}    Rate: {rate_str}/day", styles["h2"]))

    return elements


def _render_kpi_table(buckets: List[MonthBucket], styles: dict) -> list:
    """Build a 1-row × 5-column KPI mini-table summarising all buckets."""
    total_cost = Decimal("0")
    total_days = 0
    total_bonus_cost = Decimal("0")
    total_bonus_days: float = 0.0
    total_banked_hours = 0

    for bucket in buckets:
        s = bucket.summary
        total_cost += Decimal(str(s.total_cost))
        total_days += s.total_days
        total_bonus_cost += Decimal(str(s.total_bonus_cost))
        total_bonus_days += float(s.total_bonus_days)
        total_banked_hours += s.total_banked_hours

    kpi_data = [
        # Labels row
        [
            Paragraph("Total cost", styles["kpi_label"]),
            Paragraph("Worker-days", styles["kpi_label"]),
            Paragraph("Bonus cost", styles["kpi_label"]),
            Paragraph("Bonus days", styles["kpi_label"]),
            Paragraph("Banked hours", styles["kpi_label"]),
        ],
        # Values row
        [
            Paragraph(format_eur_fr(total_cost), styles["kpi_value"]),
            Paragraph(str(total_days), styles["kpi_value"]),
            Paragraph(format_eur_fr(total_bonus_cost), styles["kpi_value"]),
            Paragraph(str(total_bonus_days), styles["kpi_value"]),
            Paragraph(str(total_banked_hours), styles["kpi_value"]),
        ],
    ]

    kpi_table = Table(kpi_data, colWidths="*")
    kpi_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F4FA")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return [kpi_table]


def _render_breakdown_table(buckets: List[MonthBucket], styles: dict, usable_width: float) -> list:
    """Build per-worker breakdown table aggregated across all months."""
    agg_rows = _aggregate_across_buckets(buckets)

    # Compute column widths proportionally from weights
    total_weight = sum(_BREAKDOWN_COL_WEIGHTS)
    col_widths = [usable_width * (w / total_weight) for w in _BREAKDOWN_COL_WEIGHTS]

    # Build table data: header row + one row per worker
    header_row = _BREAKDOWN_HEADERS[:]
    table_data = [header_row]

    for agg in agg_rows:
        table_data.append(
            [
                agg.worker_name,
                str(agg.days_worked),
                str(agg.banked_hours),
                str(agg.bonus_full_days),
                str(agg.bonus_half_days),
                format_eur_fr(Decimal(str(agg.priced_cost))),
                format_eur_fr(Decimal(str(agg.bonus_cost))),
                format_eur_fr(Decimal(str(agg.total_cost))),
            ]
        )

    style_cmds = [
        # Header row styling
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        # Body font
        ("FONTNAME", (0, 1), (-1, -1), "DejaVu"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        # Borders
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        # Padding
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        # Left-align worker name column
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        # Right-align numeric/currency columns (all except first)
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (-1, 0), "CENTER"),  # header centred even for numeric
    ]

    # Alternate row shading for body rows
    for i, _ in enumerate(agg_rows):
        if i % 2 == 1:
            style_cmds.append(("BACKGROUND", (0, i + 1), (-1, i + 1), colors.HexColor("#F7F9FC")))

    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style_cmds))
    return [table]


# ---------------------------------------------------------------------------
# Footer callback
# ---------------------------------------------------------------------------


def _make_footer_callback(context: ExportContext):
    """Return a ReportLab canvas callback that draws a page-X footer.

    v1: draws "Page X" only (no X/Y — two-pass total page count is future work).
    """
    date_label = context.generated_at.strftime("%Y-%m-%d")
    project_label = context.project_name

    def _footer(canvas, doc) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont("DejaVu", 8)
        page_text = f"Page {doc.page}"
        right_text = f"{project_label} · {date_label}"

        y = 8 * mm
        canvas.drawString(15 * mm, y, page_text)
        canvas.drawRightString(A4[0] - 15 * mm, y, right_text)
        canvas.restoreState()

    return _footer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_pdf(context: ExportContext, buckets: List[MonthBucket]) -> bytes:
    """Generate a PDF document and return raw bytes.

    Args:
        context: Export metadata (project name, range, generated_at, user email).
                 When context.worker_name is set, renders single-worker view.
        buckets: One MonthBucket per calendar month in the export range.

    Returns:
        Valid PDF bytes beginning with b"%PDF-".

    Empty-range behaviour:
        If all buckets have empty summary.rows, the PDF contains only the
        header + "No labor entries in range …" paragraph (no KPI / no table).

    Single-worker mode (context.worker_name is set):
        Header includes worker name + rate line.
        KPI table and breakdown table show only that worker's aggregated rows.
        (Daily detail is omitted — same as project-wide PDF; lives only in xlsx.)
    """
    _register_fonts()

    buf = BytesIO()
    margin = 15 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=20 * mm,  # leave room for footer
    )

    # Usable page width (A4 width minus left + right margins)
    usable_width = A4[0] - 2 * margin

    styles = _make_styles()
    footer_cb = _make_footer_callback(context)

    # Determine empty-range case — summary.rows already scoped to worker by use-case
    all_empty = all(not bucket.summary.rows for bucket in buckets) if buckets else True

    story: list = []
    story.extend(_render_header(context, styles))
    story.append(Spacer(1, 6 * mm))

    if all_empty:
        from_label = context.range.from_month.strftime("%b %Y")
        to_label = context.range.to_month.strftime("%b %Y")
        story.append(
            Paragraph(
                f"No labor entries in range {from_label} → {to_label}",
                styles["body_italic"],
            )
        )
    else:
        # KPI and breakdown tables work identically for both single-worker and
        # project-wide modes because the buckets are already pre-filtered by
        # worker_id in the use-case. The breakdown table will therefore contain
        # exactly one worker row in single-worker mode.
        story.extend(_render_kpi_table(buckets, styles))
        story.append(Spacer(1, 6 * mm))
        story.extend(_render_breakdown_table(buckets, styles, usable_width))

    doc.build(story, onFirstPage=footer_cb, onLaterPages=footer_cb)
    return buf.getvalue()
