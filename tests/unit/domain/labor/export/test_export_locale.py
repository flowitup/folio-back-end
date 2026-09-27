"""The labour export renders its labels in the requested locale (en default, fr, vi)."""

from __future__ import annotations

import dataclasses
from datetime import date
from io import BytesIO

import openpyxl
import pytest
from pypdf import PdfReader

from app.domain.labor.export.labels import _LABELS, SUPPORTED_LOCALES, month_label, range_label
from app.domain.labor.export.pdf_builder import build_pdf
from app.domain.labor.export.xlsx_builder import build_xlsx
from tests.unit.domain.labor.export.test_xlsx_builder import _make_two_month_buckets


def _pdf_text(raw: bytes) -> str:
    return " ".join("".join(p.extract_text() or "" for p in PdfReader(BytesIO(raw)).pages).split())


def test_every_locale_has_the_same_label_keys():
    for locale in SUPPORTED_LOCALES:
        assert set(_LABELS[locale]) == set(_LABELS["en"]), locale


@pytest.mark.parametrize(
    ("locale", "expected"), [("en", "Apr 2026"), ("fr", "avr. 2026"), ("vi", "Tháng 4 năm 2026"), ("de", "Apr 2026")]
)
def test_month_label(locale, expected):
    assert month_label(date(2026, 4, 1), locale) == expected


def test_range_label_pluralises_in_english():
    assert range_label(date(2026, 4, 1), date(2026, 4, 1)).endswith("(1 month)")
    assert range_label(date(2026, 4, 1), date(2026, 5, 1)).endswith("(2 months)")


@pytest.mark.parametrize(
    ("locale", "sheets", "header"),
    [
        ("en", ["Summary", "Apr 2026", "May 2026"], "Worker"),
        ("fr", ["Synthèse", "avr. 2026", "mai 2026"], "Ouvrier"),
        ("vi", ["Tổng hợp", "Tháng 4 năm 2026", "Tháng 5 năm 2026"], "Nhân công"),
    ],
)
def test_xlsx_follows_the_locale(locale, sheets, header):
    ctx, buckets = _make_two_month_buckets()
    wb = openpyxl.load_workbook(BytesIO(build_xlsx(dataclasses.replace(ctx, locale=locale), buckets)))
    assert wb.sheetnames == sheets
    assert header in {c.value for row in wb[sheets[0]].iter_rows() for c in row}


def test_pdf_follows_the_locale():
    ctx, buckets = _make_two_month_buckets()
    text = _pdf_text(build_pdf(dataclasses.replace(ctx, locale="fr"), buckets))
    assert "Export main-d'œuvre" in text
    assert "Coût total" in text
    assert "Labor Export" not in text
