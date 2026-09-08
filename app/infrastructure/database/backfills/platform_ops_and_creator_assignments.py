"""Map legacy global roles onto the company-tenant model (ops flag + assignments).

Four data steps, written in Python so the same code runs on Postgres (deploy)
and SQLite (tests), each in its own module:

  1. + 2. ``platform_ops_role_mapping`` — legacy ``*:*`` holders become platform
     ops (and admin of their primary company); legacy global ``manager`` holders
     become company managers where already attached.
  3. ``creator_assignments`` — the owner bypass disappears with this release
     (D6), so every project's ``owner_id`` gets an assignment and at least the
     ``manager`` role in the project's company.
  4. ``directory_profiles`` — every ``user_company_access`` row gets an active,
     user-linked ``company_persons`` profile, so the assign-member pickers can
     see people attached before the directory shipped.

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
from app.infrastructure.database.backfills.platform_ops_role_mapping import (
    backfill_global_managers as backfill_global_managers,
    backfill_platform_ops as backfill_platform_ops,
)


def run_backfill(conn: Connection) -> BackfillReport:
    """Run all four steps in order and return the mapping report."""
    report = BackfillReport()
    backfill_platform_ops(conn, report)
    backfill_global_managers(conn, report)
    backfill_creator_assignments(conn, report)
    backfill_directory_profiles(conn, report)
    return report
