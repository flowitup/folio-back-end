"""Write user-supplied text into a worksheet without it ever becoming a formula.

openpyxl turns any string starting with "=" into a formula cell, and Excel and
LibreOffice also evaluate a value that starts with "+", "-" or "@" when the
file is edited and saved again. Every string that comes from a user (names,
notes, addresses, project names, e-mails) must go through ``safe_cell`` so a
hostile value shows up as plain text. Numbers and dates are left untouched.
"""

from __future__ import annotations

from typing import Any

from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CONTROL_PREFIXES = ("\t", "\r")


def is_formula_like(value: Any) -> bool:
    """True for a string a spreadsheet application may read as a formula."""
    if not isinstance(value, str):
        return False
    return value.startswith(_CONTROL_PREFIXES) or value.lstrip().startswith(_FORMULA_PREFIXES)


def strip_formula_lead(value: str) -> str:
    """Drop the leading characters that could make *value* look like a formula (for sheet titles)."""
    return value.lstrip("=+-@\t\r ")


def as_text(cell: Cell) -> Cell:
    """Force *cell* (whose value is already set) to a text cell when it looks like a formula."""
    if not is_formula_like(cell.value):
        return cell
    cell.data_type = "s"
    try:
        cell.quotePrefix = True
    except AttributeError:  # an openpyxl without quotePrefix: fall back to a visible apostrophe
        cell.value = "'" + cell.value
        cell.data_type = "s"
    return cell


def safe_cell(ws: Worksheet, row: int, column: int, value: Any = None) -> Cell:
    """``ws.cell(row, column, value)`` that keeps a formula-looking string as text."""
    return as_text(ws.cell(row=row, column=column, value=value))
