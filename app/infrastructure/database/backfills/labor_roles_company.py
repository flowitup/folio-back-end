"""Backfill SQL for `labor_roles.company_id` and `labor_roles.slug` (Phase 2).

`labor_roles` predates the company-as-tenant model: it was a single global
table with `UNIQUE(name)`. Migration `2ca24be9e3a8` adds `company_id`
(nullable FK) and `slug` (nullable), then tightens the uniqueness to
`UNIQUE(company_id, name)` so the same role name can exist in two companies.

Two backfill passes, in order:
  1. Slug the two seed rows created by `f2a3b4c5d6e7_add_labor_roles.py`
     (stable UUIDs `_SEED_THO_CHINH`/`_SEED_THO_PHU`) so clients can key
     i18n on a stable string instead of a UUID.
  2. Assign every row without a `company_id` to the single company in the
     database, when — and only when — exactly one company exists. Picking
     one of several would silently mis-scope pre-existing roles; those
     stay NULL for a human to assign via the admin UI, and the migration
     prints how many.

Shared by the Alembic migration and its unit test so the exact SQL that
runs at deploy time is the exact SQL exercised in CI.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Stable seed UUIDs — mirrors f2a3b4c5d6e7_add_labor_roles.py exactly.
SEED_THO_CHINH_ID = "b08f0bdb-9e78-40ca-aca9-96016de45c7c"
SEED_THO_PHU_ID = "de417d58-3d38-4658-a5f7-02b51fb749fc"
SEED_THO_CHINH_SLUG = "tho_chinh"
SEED_THO_PHU_SLUG = "tho_phu"

_SLUG_SEED_ROWS_SQL = text(
    """
    UPDATE labor_roles SET slug = :slug
    WHERE REPLACE(LOWER(CAST(id AS TEXT)), '-', '') = REPLACE(LOWER(:id), '-', '')
      AND slug IS NULL
    """
)

_BACKFILL_COMPANY_WHEN_SOLE_SQL = text(
    """
    UPDATE labor_roles
    SET company_id = (SELECT id FROM companies LIMIT 1)
    WHERE company_id IS NULL
      AND (SELECT COUNT(*) FROM companies) = 1
    """
)

_COUNT_NULL_COMPANY_ID_SQL = text("SELECT COUNT(*) FROM labor_roles WHERE company_id IS NULL")


def slug_seed_rows(conn: Connection) -> None:
    """Pass 1: set `slug` on the two stable seed rows, if present and unset.

    Matches the id dash/case-insensitively (`REPLACE(LOWER(...), '-', '')`)
    rather than with plain `=` — on SQLite (test suite), `PG_UUID(as_uuid=True)`
    columns are plain CHAR/TEXT, and different insert paths across this
    codebase (ORM vs raw `text()` SQL) do not agree on hex-vs-dashed
    formatting. Same normalization technique as
    `SqlAlchemyAuthzReader._norm()`. A no-op cost on Postgres (native `uuid`
    column, two rows total)."""
    conn.execute(_SLUG_SEED_ROWS_SQL, {"slug": SEED_THO_CHINH_SLUG, "id": SEED_THO_CHINH_ID})
    conn.execute(_SLUG_SEED_ROWS_SQL, {"slug": SEED_THO_PHU_SLUG, "id": SEED_THO_PHU_ID})


def backfill_company_when_sole(conn: Connection) -> None:
    """Pass 2: assign every NULL `company_id` row to the sole company, when exactly one exists."""
    conn.execute(_BACKFILL_COMPANY_WHEN_SOLE_SQL)


def count_null_company_id(conn: Connection) -> int:
    """Return how many `labor_roles` rows still have no `company_id`."""
    return conn.execute(_COUNT_NULL_COMPANY_ID_SQL).scalar_one()


def run_backfill(conn: Connection) -> int:
    """Run both passes in order; return the count still NULL afterward."""
    slug_seed_rows(conn)
    backfill_company_when_sole(conn)
    return count_null_company_id(conn)
