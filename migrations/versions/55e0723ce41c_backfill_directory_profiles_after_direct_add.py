"""backfill directory profiles for accounts attached by a direct add

Adding an existing account to a project through POST /invitations attached
it to the project's company without a `company_persons` profile, so the
assign-member pickers, which read the directory, never offered it. The
direct-add path now creates the profile; this data step gives the accounts
attached that way before the fix their profile. It reuses the idempotent
backfill of migration 9a4c1e7b2d05: every `user_company_access` row gets an
active, user-linked profile, existing ones are left alone.

Revision ID: 55e0723ce41c
Revises: 9a1c7e5f3b2d
Create Date: 2026-09-27
"""

from alembic import op

from app.infrastructure.database.backfills.authz_backfill_report import BackfillReport
from app.infrastructure.database.backfills.directory_profiles import backfill_directory_profiles

# revision identifiers, used by Alembic.
revision = "55e0723ce41c"
down_revision = "9a1c7e5f3b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    report = BackfillReport()
    backfill_directory_profiles(op.get_bind(), report)
    print(
        f"directory profiles backfill: {report.profiles_created} created, "
        f"{report.profiles_reactivated} reactivated, {report.persons_created} person(s) created"
    )
    # Never abort on warnings: migrations run at container start.
    for warning in report.warnings:
        print(warning)


def downgrade() -> None:
    # Data-only fill; backfilled profiles are indistinguishable from later ones.
    pass
