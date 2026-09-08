"""Relationship backfills for the company-tenant model.

Two data steps, written in Python so the same code runs on Postgres (deploy)
and SQLite (tests), each in its own module:

  1. ``creator_assignments`` — the owner bypass is gone (D6), so every project's
     ``owner_id`` gets an assignment and at least the ``manager`` role in the
     project's company.
  2. ``directory_profiles`` — every ``user_company_access`` row gets an active,
     user-linked ``company_persons`` profile, so the assign-member pickers can
     see people attached before the directory shipped.

The legacy-role mapping that used to run first lives in migration
``9a4c1e7b2d05`` itself: it reads tables a later revision drops.

Every step is idempotent: re-running changes nothing. The returned report is
printed by the migration so the mapping can be reviewed against a prod dump;
`report.warnings` carries what needs a human decision (projects with no company,
directory profiles created without their phone).
"""

from __future__ import annotations

from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport as BackfillReport
from app.infrastructure.database.backfills.creator_assignments import (
    backfill_creator_assignments as backfill_creator_assignments,
)
from app.infrastructure.database.backfills.directory_profiles import (
    backfill_directory_profiles as backfill_directory_profiles,
)


def run_backfill(conn: Connection, report: "BackfillReport | None" = None) -> BackfillReport:
    """Run both steps in order and return the mapping report.

    Pass an existing `report` to append to it (migration 9a4c1e7b2d05 starts
    one for its own legacy-role steps).
    """
    report = report if report is not None else BackfillReport()
    backfill_creator_assignments(conn, report)
    backfill_directory_profiles(conn, report)
    return report
