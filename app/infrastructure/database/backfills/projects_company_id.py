"""Backfill SQL for orphaned `projects.company_id` rows (H2).

Migration b1c2d3e4f5a6 backfilled `projects.company_id` from the owner's
`is_primary=TRUE` `user_company_access` row inline, using a Postgres-only
`UPDATE ... FROM` statement — rows whose owner had no primary company at the
time (or that never went through that migration, e.g. rows inserted before
`POST /projects` started setting `company_id` at creation) are still NULL.

This module is imported by BOTH the follow-up Alembic migration and its unit
test, so the exact SQL exercised by the test is the exact SQL Alembic runs.
Both statements use a portable correlated subquery (works on SQLite and
Postgres identically) rather than Postgres' `UPDATE ... FROM` — a one-off
backfill isn't performance-sensitive enough to need dialect branching.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Pass 1: owner's `is_primary=TRUE` access row (mirrors migration b1c2d3e4f5a6's
# original backfill — re-run here for any project that missed it).
_BACKFILL_FROM_PRIMARY_COMPANY_SQL = text(
    """
    UPDATE projects
    SET company_id = (
        SELECT uca.company_id
        FROM user_company_access uca
        WHERE uca.user_id = projects.owner_id AND uca.is_primary = TRUE
    )
    WHERE projects.company_id IS NULL
      AND EXISTS (
        SELECT 1 FROM user_company_access uca
        WHERE uca.user_id = projects.owner_id AND uca.is_primary = TRUE
      )
    """
)

# Pass 2: for rows STILL NULL after pass 1 (owner has no primary flag set),
# fall back to the owner's company ONLY when they belong to exactly one —
# picking one of several would silently mis-scope the project.
_BACKFILL_FROM_SOLE_COMPANY_SQL = text(
    """
    UPDATE projects
    SET company_id = (
        SELECT uca.company_id
        FROM user_company_access uca
        WHERE uca.user_id = projects.owner_id
        LIMIT 1
    )
    WHERE projects.company_id IS NULL
      AND (
        SELECT COUNT(DISTINCT uca.company_id)
        FROM user_company_access uca
        WHERE uca.user_id = projects.owner_id
      ) = 1
    """
)

_COUNT_NULL_COMPANY_ID_SQL = text("SELECT COUNT(*) FROM projects WHERE company_id IS NULL")


def backfill_from_primary_company(conn: Connection) -> None:
    """Pass 1: set `company_id` from the owner's `is_primary=TRUE` access row."""
    conn.execute(_BACKFILL_FROM_PRIMARY_COMPANY_SQL)


def backfill_from_sole_company(conn: Connection) -> None:
    """Pass 2: for rows still NULL, set `company_id` when the owner has exactly one company."""
    conn.execute(_BACKFILL_FROM_SOLE_COMPANY_SQL)


def count_null_company_id(conn: Connection) -> int:
    """Return how many `projects` rows still have no `company_id`."""
    return conn.execute(_COUNT_NULL_COMPANY_ID_SQL).scalar_one()


def run_backfill(conn: Connection) -> int:
    """Run both backfill passes in order; return the count still NULL afterward.

    `projects.company_id` stays nullable — any remainder needs a human to
    pick a company (ambiguous owner, or an owner with zero company_access
    rows at all) via the admin project-settings UI.
    """
    backfill_from_primary_company(conn)
    backfill_from_sole_company(conn)
    return count_null_company_id(conn)
