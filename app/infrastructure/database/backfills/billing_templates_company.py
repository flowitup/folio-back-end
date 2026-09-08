"""Backfill SQL for `billing_document_templates.company_id` (Phase 2).

Templates were user-owned only (`UNIQUE(user_id, kind, name)`); migration
`2ca24be9e3a8` adds `company_id` (nullable FK to `companies`) and tightens
uniqueness to `UNIQUE(company_id, user_id, kind, name)` so templates can be
listed per company while staying authored by a user.

Two backfill passes, in order:
  1. Owner's `is_primary=TRUE` `user_company_access` row.
  2. For rows still NULL, the owner's company when they belong to exactly
     one — picking one of several would silently mis-scope the template.

Rows still NULL after both passes (owner has no company access row, or an
ambiguous set with none flagged primary) stay NULL and are excluded from
company-scoped listings; the migration prints how many.

Shared by the Alembic migration and its unit test — same SQL in CI as at
deploy time. Mirrors `app.infrastructure.database.backfills.projects_company_id`.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

_BACKFILL_FROM_PRIMARY_COMPANY_SQL = text(
    """
    UPDATE billing_document_templates
    SET company_id = (
        SELECT uca.company_id
        FROM user_company_access uca
        WHERE uca.user_id = billing_document_templates.user_id AND uca.is_primary = TRUE
    )
    WHERE billing_document_templates.company_id IS NULL
      AND EXISTS (
        SELECT 1 FROM user_company_access uca
        WHERE uca.user_id = billing_document_templates.user_id AND uca.is_primary = TRUE
      )
    """
)

_BACKFILL_FROM_SOLE_COMPANY_SQL = text(
    """
    UPDATE billing_document_templates
    SET company_id = (
        SELECT uca.company_id
        FROM user_company_access uca
        WHERE uca.user_id = billing_document_templates.user_id
        LIMIT 1
    )
    WHERE billing_document_templates.company_id IS NULL
      AND (
        SELECT COUNT(DISTINCT uca.company_id)
        FROM user_company_access uca
        WHERE uca.user_id = billing_document_templates.user_id
      ) = 1
    """
)

_COUNT_NULL_COMPANY_ID_SQL = text("SELECT COUNT(*) FROM billing_document_templates WHERE company_id IS NULL")


def backfill_from_primary_company(conn: Connection) -> None:
    """Pass 1: set `company_id` from the owner's `is_primary=TRUE` access row."""
    conn.execute(_BACKFILL_FROM_PRIMARY_COMPANY_SQL)


def backfill_from_sole_company(conn: Connection) -> None:
    """Pass 2: for rows still NULL, set `company_id` when the owner has exactly one company."""
    conn.execute(_BACKFILL_FROM_SOLE_COMPANY_SQL)


def count_null_company_id(conn: Connection) -> int:
    """Return how many `billing_document_templates` rows still have no `company_id`."""
    return conn.execute(_COUNT_NULL_COMPANY_ID_SQL).scalar_one()


def run_backfill(conn: Connection) -> int:
    """Run both backfill passes in order; return the count still NULL afterward."""
    backfill_from_primary_company(conn)
    backfill_from_sole_company(conn)
    return count_null_company_id(conn)
