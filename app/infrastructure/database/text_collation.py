"""Accent-aware sorting and accent-insensitive search for user text.

The database collation is byte order (C.UTF-8), so 'électricité' sorts after
'zone' and LIKE 'beton' never matches 'béton'. Two helpers fix that without a
migration:

* ``sortable(col, session)``: on PostgreSQL with ICU, ``col COLLATE "und-x-icu"``
  (the CLDR root order: accents and case are minor differences, so 'Été' sits next
  to 'Etude'); elsewhere ``lower(col)`` as before.
* ``folded(col, session)`` / ``fold_text(value)``: lower case and strip Latin
  accents (French, Vietnamese 'ư', 'ơ', 'đ'...) on both sides of a LIKE. These are the
  app's one fold rule (``text_fold``, ``app.domain.value_objects.accent_fold``):
  ``translate()`` on PostgreSQL (``unaccent`` is not installed), plain ``lower`` on
  SQLite (tests).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.infrastructure.database.text_fold import fold_text
from app.infrastructure.database.text_fold import folded as folded_expr

__all__ = ["ICU_COLLATION", "fold_text", "folded", "has_icu_collation", "sortable"]

ICU_COLLATION = "und-x-icu"


_icu_available: dict[str, bool] = {}


def _dialect(session: Session) -> str:
    return str(session.get_bind().dialect.name)


def folded(col: Any, session: Session) -> Any:
    """SQL expression of ``col`` folded like ``fold_text`` (the shared ``text_fold.folded``)."""
    return folded_expr(col)


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
