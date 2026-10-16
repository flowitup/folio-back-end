"""Accent- and case-insensitive text search helpers.

Users often type without accents (phone keyboards, English or Vietnamese
layouts): 'platre' must find 'Plâtre', 'lefevre' 'Lefèvre', 'ong nhua'
'Ống nhựa'. Postgres has no ``unaccent`` extension here (it would need a
migration), so the column is folded with ``translate()`` over a fixed table of
accented Latin letters (Latin-1, Latin Extended-A/B and the Vietnamese
Latin Extended Additional block), then lower-cased. The search term is folded
in Python with the very same table, so both sides always agree.

Other dialects (the SQLite test database) compile to a plain ``lower()``.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import String

_SPECIAL = {"đ": "d", "Đ": "d", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "ı": "i"}


def _build_table() -> tuple[str, str]:
    src: list[str] = []
    dst: list[str] = []
    ranges = [(0x00C0, 0x024F), (0x1E00, 0x1EFF)]
    for start, end in ranges:
        for cp in range(start, end + 1):
            ch = chr(cp)
            if ch in _SPECIAL:
                src.append(ch)
                dst.append(_SPECIAL[ch])
                continue
            base = unicodedata.normalize("NFKD", ch)[:1]
            if base != ch and base.isascii() and base.isalpha():
                src.append(ch)
                dst.append(base.lower())
    return "".join(src), "".join(dst)


FOLD_FROM, FOLD_TO = _build_table()
_FOLD_MAP = str.maketrans(FOLD_FROM, FOLD_TO)


def fold_text(value: str) -> str:
    """Fold *value* exactly like :class:`folded` folds a column."""
    return value.translate(_FOLD_MAP).lower()


def folded_like_pattern(term: str) -> str:
    """Return an escaped ``%term%`` LIKE pattern for the folded *term* (escape '\\')."""
    escaped = fold_text(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class folded(FunctionElement[str]):
    """SQL expression: the column without accents, lower-cased."""

    type = String()
    inherit_cache = True
    name = "folded"


@compiles(folded, "postgresql")
def _compile_folded_pg(element: folded, compiler: Any, **kw: Any) -> str:
    arg = compiler.process(list(element.clauses)[0], **kw)
    src = FOLD_FROM.replace("'", "''")
    dst = FOLD_TO.replace("'", "''")
    return f"lower(translate({arg}, '{src}', '{dst}'))"


@compiles(folded)
def _compile_folded_default(element: folded, compiler: Any, **kw: Any) -> str:
    arg = compiler.process(list(element.clauses)[0], **kw)
    return f"lower({arg})"
