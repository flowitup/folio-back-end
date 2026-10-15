"""The shared spreadsheet text guard."""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from app.domain.services import xlsx_safe_cell
from app.domain.services.xlsx_safe_cell import as_text, is_formula_like, safe_cell, strip_formula_lead


@pytest.mark.parametrize("value", ["=1", "+1", "-1", "@x", "\t1", "\r1", " =1", "\n+1", " =1"])
def test_formula_like_strings(value):
    assert is_formula_like(value)


@pytest.mark.parametrize("value", ["", "ACME", "a=b", "x-1", 5, 1.5, None, "1 - 2"])
def test_plain_values_are_not_formula_like(value):
    assert not is_formula_like(value)


def test_safe_cell_writes_text_with_quote_prefix_and_leaves_numbers():
    ws = Workbook().active
    text = safe_cell(ws, 1, 1, "=1+1")
    number = safe_cell(ws, 2, 1, -3)
    assert (text.data_type, text.quotePrefix, text.value) == ("s", True, "=1+1")
    assert (number.data_type, number.value) == ("n", -3)


def test_apostrophe_fallback_when_quote_prefix_is_unavailable():
    class _Cell:
        __slots__ = ("value", "data_type")

        def __init__(self, value):
            self.value = value
            self.data_type = "f"

    cell = as_text(_Cell("=1+1"))
    assert (cell.value, cell.data_type) == ("'=1+1", "s")


def test_strip_formula_lead():
    assert strip_formula_lead(" =+-@\t\rBob") == "Bob"
    assert xlsx_safe_cell.strip_formula_lead("Bob") == "Bob"
