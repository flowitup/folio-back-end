"""Literal substring patterns for SQL LIKE searches.

A user's search text goes into a LIKE pattern, where `%` and `_` are wildcards: an
unescaped `%` matches every row and `_` any single character. Escape them (and the
escape character itself) and pass `escape=LIKE_ESCAPE` to `.like()`/`.ilike()` so the
text only matches itself, on PostgreSQL and SQLite alike.
"""

from __future__ import annotations

LIKE_ESCAPE = "\\"


def escape_like(value: str) -> str:
    """Escape LIKE wildcards in `value` so it matches only its literal characters."""
    # The escape character first, then the wildcards.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def contains_pattern(value: str) -> str:
    """LIKE pattern matching any text that contains `value` literally."""
    return f"%{escape_like(value)}%"
