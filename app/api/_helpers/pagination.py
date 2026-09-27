"""Shared parsing of ``?limit=&offset=`` list query parameters.

A negative LIMIT/OFFSET, or an offset past Postgres BIGINT, fails in the
database with a 500; these bounds turn such values into a 400 instead.
"""

from __future__ import annotations

from typing import Mapping

MAX_OFFSET = 10**9
MAX_PAGE = 10**6


def parse_limit_offset(args: Mapping[str, str], *, default_limit: int = 50, max_limit: int = 200) -> tuple[int, int]:
    """Return (limit, offset); limit is clamped to ``max_limit``.

    Raises ValueError with a client-facing message when either value is not an
    integer, the limit is below 1 or the offset is outside 0..MAX_OFFSET.
    """
    try:
        limit = int(args.get("limit", default_limit))
        offset = int(args.get("offset", 0))
    except (TypeError, ValueError):
        raise ValueError("limit and offset must be integers")
    if limit < 1:
        raise ValueError("limit must be a positive integer")
    if not 0 <= offset <= MAX_OFFSET:
        raise ValueError(f"offset must be between 0 and {MAX_OFFSET}")
    return min(limit, max_limit), offset
