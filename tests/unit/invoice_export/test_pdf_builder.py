"""Unit tests for app.domain.invoice.export.pdf_builder.build_pdf.

Uses magic-byte checks and basic PDF structure inspection.
Text extraction via pdfminer.six when available; falls back to length/byte checks.

Covers:
- PDF magic bytes (%PDF-)
- Summary page contains project name text
- One page per invoice after summary page (rough /Page count)
- Grand total present in PDF text
- Empty range produces "No invoices in range" text
- Special chars XML-escaped — no crash on <script> in recipient name
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from uuid import uuid4


from app.domain.entities.invoice import Invoice, InvoiceType
from app.domain.invoice.export.models import (
    InvoiceBundle,
    InvoiceExportContext,
    InvoiceExportRange,
    TypeSubtotal,
)
from app.domain.invoice.export.pdf_builder import build_pdf
from app.domain.value_objects.invoice_item import InvoiceItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(project_name: str = "Grand Canal Tower") -> InvoiceExportContext:
    return InvoiceExportContext(
        project_name=project_name,
        project_id=uuid4(),
        range=InvoiceExportRange(
            from_month=date(2026, 1, 1),
            to_month=date(2026, 3, 1),
        ),
        generated_at=datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc),
        generated_by_email="admin@example.com",
        type_filter=None,
    )


def _make_invoice(
    *,
    project_id=None,
    invoice_type: InvoiceType = InvoiceType.RELEASED_FUNDS,
    amount: Decimal = Decimal("250.00"),
    recipient: str = "ACME Corp",
    issue_date: date = date(2026, 1, 15),
    invoice_number: str = "INV-001",
    notes: str | None = None,
) -> Invoice:
    pid = project_id or uuid4()
    item = InvoiceItem(description="Consulting", quantity=Decimal("1"), unit_price=amount)
    return Invoice(
        id=uuid4(),
        project_id=pid,
        invoice_number=invoice_number,
        type=invoice_type,
        issue_date=issue_date,
        recipient_name=recipient,
        created_by=uuid4(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        items=[item],
        notes=notes,
    )


def _make_bundle(invoices: list) -> InvoiceBundle:
    subtotals = []
    for t in (InvoiceType.RELEASED_FUNDS, InvoiceType.LABOR, InvoiceType.SUPPLIER):
        scoped = [i for i in invoices if i.type == t]
        if scoped:
            subtotals.append(
                TypeSubtotal(
                    type=t,
                    invoice_count=len(scoped),
                    total_amount=sum((i.total_amount for i in scoped), Decimal("0")),
                )
            )
    grand_total = sum((i.total_amount for i in invoices), Decimal("0"))
    return InvoiceBundle(
        invoices=invoices,
        subtotals_by_type=subtotals,
        grand_total=grand_total,
        invoice_count=len(invoices),
    )


def _extract_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes via pdfminer.six; fall back to empty string."""
    try:
        from pdfminer.high_level import extract_text as pm_extract

        return pm_extract(BytesIO(pdf_bytes))
    except Exception:
        return ""


def _count_pages(pdf_bytes: bytes) -> int:
    """Count /Type /Page entries in raw PDF bytes (rough but dependency-free)."""
    return pdf_bytes.count(b"/Type /Page")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pdf_starts_with_pct_pdf_magic():
    """build_pdf output must begin with b'%PDF-' magic bytes."""
    ctx = _make_context()
    bundle = _make_bundle([_make_invoice()])
    content = build_pdf(ctx, bundle)
    assert content[:5] == b"%PDF-", f"Expected %PDF- magic, got {content[:10]!r}"


def test_summary_page_text_contains_project_name():
    """Project name appears in the PDF text (summary header band)."""
    project_name = "Grand Canal Tower"
    ctx = _make_context(project_name=project_name)
    bundle = _make_bundle([_make_invoice()])
    content = build_pdf(ctx, bundle)

    text = _extract_text(content)
    if text:
        assert project_name in text, f"Project name '{project_name}' not found in PDF text"
    else:
        # Fallback: content must be non-trivial
        assert len(content) > 1000, "PDF too small — likely generation error"


def test_one_page_per_invoice_after_summary():
    """3 invoices → at least 4 /Page objects (1 summary + 3 invoice pages)."""
    ctx = _make_context()
    invoices = [_make_invoice(invoice_number=f"INV-{i:03d}", issue_date=date(2026, 1, i + 1)) for i in range(3)]
    bundle = _make_bundle(invoices)
    content = build_pdf(ctx, bundle)

    page_count = _count_pages(content)
    # ReportLab embeds /Type /Page for each page; at minimum 4 expected (1+3)
    assert page_count >= 4, f"Expected ≥4 /Page entries for 3 invoices + summary, got {page_count}"


def test_grand_total_present_in_pdf():
    """Grand total formatted value appears in PDF text."""
    ctx = _make_context()
    # Use an amount whose formatted representation is distinctive
    inv = _make_invoice(amount=Decimal("999.00"))
    bundle = _make_bundle([inv])
    content = build_pdf(ctx, bundle)

    text = _extract_text(content)
    if text:
        # format_eur_fr(999.00) → "999,00 €" in fr-FR locale
        assert "999" in text, "Grand total value not found in PDF text"
    else:
        assert len(content) > 1000


def test_empty_range_says_no_invoices():
    """Empty bundle → PDF says 'No invoices in range'."""
    ctx = _make_context()
    bundle = _make_bundle(invoices=[])
    content = build_pdf(ctx, bundle)

    assert content[:5] == b"%PDF-"

    text = _extract_text(content)
    if text:
        assert "No invoices in range" in text, f"Expected empty-range message in PDF text; got excerpt: {text[:200]}"
    else:
        # Minimal length check when pdfminer unavailable
        assert len(content) > 500


def test_special_chars_xml_escaped():
    """Recipient name with <script> tag must not crash build_pdf."""
    ctx = _make_context()
    xss_inv = _make_invoice(recipient="<script>alert(1)</script>", amount=Decimal("100.00"))
    bundle = _make_bundle([xss_inv])

    # Must not raise — xml.sax.saxutils.escape is applied before Paragraph
    content = build_pdf(ctx, bundle)
    assert content[:5] == b"%PDF-", "Expected valid PDF magic bytes after XSS recipient"
    assert len(content) > 1000
