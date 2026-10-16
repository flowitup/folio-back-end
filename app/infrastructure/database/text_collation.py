"""Accent-aware sorting and accent-insensitive search for user text.

The database collation is byte order (C.UTF-8), so 'électricité' sorts after
'zone' and LIKE 'beton' never matches 'béton'. Two helpers fix that without a
migration:

* ``sortable(col, session)``: on PostgreSQL with ICU, ``col COLLATE "und-x-icu"``
  (the CLDR root order: accents and case are minor differences, so 'Été' sits next
  to 'Etude'); elsewhere ``lower(col)`` as before.
* ``folded(col, session)`` / ``fold_text(value)``: lower case and strip Latin
  accents (French, Vietnamese 'ư', 'ơ', 'đ'...) on both sides of a LIKE, through
  ``translate()`` on PostgreSQL (``unaccent`` is not installed) and plain ``lower``
  on SQLite (tests).
"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

ICU_COLLATION = "und-x-icu"


def _build_fold_map() -> tuple[str, str]:
    src: list[str] = []
    dst: list[str] = []
    ranges = [(0x00C0, 0x024F), (0x1E00, 0x1EFF)]  # Latin-1 Supplement, Extended-A/B, Extended Additional
    for start, end in ranges:
        for code in range(start, end + 1):
            char = chr(code)
            if not char.isalpha():
                continue
            base = unicodedata.normalize("NFKD", char)[0].lower()
            if base.isascii() and base.isalpha() and base != char:
                src.append(char)
                dst.append(base)
    for special, base in (("đ", "d"), ("Đ", "d"), ("ø", "o"), ("Ø", "o"), ("ł", "l"), ("Ł", "l")):
        if special not in src:
            src.append(special)
            dst.append(base)
    return "".join(src), "".join(dst)


_FOLD_FROM, _FOLD_TO = _build_fold_map()
_FOLD_TABLE = str.maketrans(_FOLD_FROM, _FOLD_TO)

_icu_available: dict[str, bool] = {}


def _dialect(session: Session) -> str:
    return str(session.get_bind().dialect.name)


def fold_text(value: str) -> str:
    """Lower-case ``value`` and strip its Latin accents ('Hương' -> 'huong')."""
    return unicodedata.normalize("NFC", value).lower().translate(_FOLD_TABLE)


def folded(col: Any, session: Session) -> Any:
    """SQL expression of ``col`` folded like ``fold_text`` (accents kept on SQLite)."""
    if _dialect(session) == "postgresql":
        return func.translate(func.lower(col), _FOLD_FROM, _FOLD_TO)
    return func.lower(col)


def has_icu_collation(session: Session) -> bool:
    """Whether the server offers the ICU root collation (checked once per database)."""
    if _dialect(session) != "postgresql":
        return False
    key = str(session.get_bind().url)
    if key not in _icu_available:
        try:
            found = session.execute(
                text("SELECT 1 FROM pg_collation WHERE collname = :name"), {"name": ICU_COLLATION}
            ).first()
            _icu_available[key] = found is not None
        except Exception:
            _icu_available[key] = False
    return _icu_available[key]


def sortable(col: Any, session: Session) -> Any:
    """ORDER BY expression giving a natural French/Vietnamese order for ``col``."""
    if has_icu_collation(session):
        return col.collate(ICU_COLLATION)
    return func.lower(col)
