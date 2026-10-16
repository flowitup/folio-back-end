"""Accent- and case-insensitive text search helpers.

Users often type without accents (phone keyboards, English or Vietnamese
layouts): 'platre' must find 'Plâtre', 'lefevre' 'Lefèvre', 'ong nhua'
'Ống nhựa'. Postgres has no ``unaccent`` extension here (it would need a
migration), so the column is folded with ``translate()`` over a fixed table of
accented Latin letters (Latin-1, Latin Extended-A/B and the Vietnamese
Latin Extended Additional block), then lower-cased. The search term is folded
in Python with the very same table, so both sides always agree. The table and the
Python fold live in ``app.domain.value_objects.accent_fold`` (one rule app-wide); both
sides are NFC-normalized first, so decomposed accents fold too.

Other dialects (the SQLite test database) compile to a plain ``lower()``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import String

from app.domain.value_objects.accent_fold import FOLD_FROM, FOLD_TO, fold_text

__all__ = ["FOLD_FROM", "FOLD_TO", "fold_text", "folded", "folded_like_pattern"]


def folded_like_pattern(term: str) -> str:
    """Return an escaped ``%term%`` LIKE pattern for the folded *term* (escape '\\')."""
    escaped = fold_text(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class folded(FunctionElement[str]):
    """SQL expression: the column NFC-normalized, without accents, lower-cased (``fold_text``)."""

    type = String()
    inherit_cache = True
    name = "folded"


@compiles(folded, "postgresql")
def _compile_folded_pg(element: folded, compiler: Any, **kw: Any) -> str:
    arg = compiler.process(list(element.clauses)[0], **kw)
    src = FOLD_FROM.replace("'", "''")
    dst = FOLD_TO.replace("'", "''")
    return f"lower(translate(normalize({arg}, NFC), '{src}', '{dst}'))"


@compiles(folded)
def _compile_folded_default(element: folded, compiler: Any, **kw: Any) -> str:
    arg = compiler.process(list(element.clauses)[0], **kw)
    return f"lower({arg})"
