"""backfill orphaned projects.company_id rows

Migration b1c2d3e4f5a6 backfilled `projects.company_id` from the owner's
`is_primary=TRUE` `user_company_access` row inline, but only via a
Postgres-only `UPDATE ... FROM` and only once, at that migration's time —
projects whose owner had no primary company flagged then (or that were
inserted afterward through a path that skipped setting `company_id`) are
still NULL. A NULL `company_id` means the tenancy fixes in this phase (D8
resolver, company-scoped listing) can't place the project in any company —
its admin sees it nowhere.

Two passes (see app.infrastructure.database.backfills.projects_company_id,
shared with this migration's unit test so the exact SQL run here is the
exact SQL exercised there):
  1. owner's `is_primary=TRUE` access row (re-run of the original backfill).
  2. for rows still NULL, the owner's company when they belong to exactly
     one — picking one of several would silently mis-scope the project.

The column stays nullable: any project still NULL after both passes has an
ambiguous or missing owner-company relationship and needs a human decision
(admin project-settings UI), not a guess.

Revision ID: 15c1df3fdbfa
Revises: 61f2c832b3b4
Create Date: 2026-09-08 00:00:00.000000

"""

from alembic import op

from app.infrastructure.database.backfills.projects_company_id import run_backfill

# revision identifiers, used by Alembic.
revision = "15c1df3fdbfa"
down_revision = "61f2c832b3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    still_null = run_backfill(conn)
    print(f"projects.company_id backfill: {still_null} project(s) still have no company_id after both passes")


def downgrade() -> None:
    # No-op: this migration only fills in previously-NULL data, it never
    # overwrites an existing value. There is nothing to distinguish "this
    # migration set it" from "it was already set" to reverse, and the
    # column was already nullable before this revision — downgrading the
    # schema requires no change.
    pass
