"""Dialect-aware UUID comparison for Core queries that join two UUID columns.

On PostgreSQL every id column is a native `uuid`, so a plain `=` is correct and
index-friendly. On SQLite (the test suite) the same columns are TEXT and this
codebase writes them through several insert paths that disagree on
hex-vs-dashed formatting (ORM `UUID(as_uuid=True)` TypeDecorator, Core
`Table.insert()`, raw `text()` SQL with `str(uuid)`), so a column-to-column `=`
silently matches nothing. Normalizing both sides is the same trade
`SqlAlchemyAuthzReader` makes: correctness over index use, on test-only row
volumes.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Text, cast, func, literal
from sqlalchemy.orm import Session


def is_sqlite(session: Session) -> bool:
    """True when the session is bound to SQLite."""
    return session.get_bind().dialect.name == "sqlite"


def normalized(value):
    """Lowercased, dash-stripped text form of a UUID column or value.

    A plain `UUID` is normalized in Python (SQLite cannot bind a `UUID`
    object), a column expression in SQL.
    """
    if isinstance(value, UUID):
        return literal(value.hex)
    return func.replace(func.lower(cast(value, Text)), "-", "")


def uuid_eq(sqlite: bool, left, right):
    """Comparison between two UUID columns/values, normalized on SQLite only."""
    if sqlite:
        return normalized(left) == normalized(right)
    return left == right
