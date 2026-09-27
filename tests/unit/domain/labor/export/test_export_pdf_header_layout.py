"""Breakdown table headers wrap inside their columns instead of overflowing into the next one."""

from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import Paragraph

from app.domain.labor.export import pdf_builder
from app.domain.labor.export.pdf_builder import _BREAKDOWN_COL_WEIGHTS, _BREAKDOWN_HEADERS, _make_styles

_CELL_PADDING = 8  # LEFTPADDING + RIGHTPADDING of the breakdown table


def test_every_header_word_fits_its_column():
    styles = _make_styles()
    usable_width = A4[0] - 2 * 15 * mm
    total = sum(_BREAKDOWN_COL_WEIGHTS)
    for label, weight in zip(_BREAKDOWN_HEADERS, _BREAKDOWN_COL_WEIGHTS):
        room = usable_width * weight / total - _CELL_PADDING
        th = styles["th"]
        widest = max(stringWidth(word, th.fontName, th.fontSize) for word in label.split())
        assert widest <= room, f"{label!r}: {widest:.1f}pt word in a {room:.1f}pt column"


def test_header_cells_are_wrapping_paragraphs():
    cells = pdf_builder._header_cells(_BREAKDOWN_HEADERS, _make_styles())
    assert all(isinstance(c, Paragraph) for c in cells)
