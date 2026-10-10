"""Exports say when they were made on the Paris clock, never as raw UTC or an ISO string."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from io import BytesIO

import openpyxl
from pypdf import PdfReader

from app.domain.labor.export.format import format_generated_at
from app.domain.labor.export.pdf_builder import build_pdf
from app.domain.labor.export.xlsx_builder import build_xlsx
from tests.unit.domain.labor.export.test_xlsx_builder import _make_two_month_buckets

SUMMER_UTC = datetime(2026, 10, 9, 18, 16, tzinfo=timezone.utc)  # 20:16 in Paris
WINTER_UTC = datetime(2026, 12, 31, 23, 30, tzinfo=timezone.utc)  # 00:30 next day in Paris


def test_format_generated_at_uses_paris_time():
    assert format_generated_at(SUMMER_UTC) == "09/10/2026 20:16"
    assert format_generated_at(WINTER_UTC) == "01/01/2027 00:30"
    assert format_generated_at(WINTER_UTC, with_time=False) == "01/01/2027"
    # A naive timestamp is read as UTC, as the app stores them.
    assert format_generated_at(datetime(2026, 10, 9, 18, 16)) == "09/10/2026 20:16"


def test_labor_xlsx_header_prints_paris_time():
    ctx, buckets = _make_two_month_buckets()
    ctx = dataclasses.replace(ctx, generated_at=SUMMER_UTC, locale="fr")
    wb = openpyxl.load_workbook(BytesIO(build_xlsx(ctx, buckets)))
    a4 = wb[wb.sheetnames[0]]["A4"].value
    assert a4.startswith("Généré le 09/10/2026 20:16 par ")
    assert "+00:00" not in a4 and "T18:16" not in a4


def test_labor_pdf_prints_paris_time_without_utc():
    ctx, buckets = _make_two_month_buckets()
    ctx = dataclasses.replace(ctx, generated_at=WINTER_UTC, locale="fr")
    raw = build_pdf(ctx, buckets)
    text = " ".join("".join(p.extract_text() or "" for p in PdfReader(BytesIO(raw)).pages).split())
    assert "Généré le 01/01/2027 00:30" in text
    assert "UTC" not in text and "31/12/2026" not in text
