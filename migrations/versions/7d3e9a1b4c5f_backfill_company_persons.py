"""backfill company_persons for persons that predate the company directory

`2ca24be9e3a8` created `company_persons` without giving existing persons a
profile, so a deployed database starts with an empty company directory and an
empty company-scoped persons search even though those people already work on
the company's projects. See
app.infrastructure.database.backfills.company_persons_from_workers (shared with
the unit test): pass 1 derives (company, person) from workers → projects,
pass 2 attaches the rest to the single company when exactly one exists.

Revision ID: 7d3e9a1b4c5f
Revises: 2ca24be9e3a8
Create Date: 2026-09-08 08:10:00.000000
"""

from alembic import op

from app.infrastructure.database.backfills.company_persons_from_workers import run_backfill

# revision identifiers, used by Alembic.
revision = "7d3e9a1b4c5f"
down_revision = "2ca24be9e3a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from_workers, from_sole, still_missing = run_backfill(op.get_bind())
    print(
        f"company_persons backfill: {from_workers} profile(s) from workers, "
        f"{from_sole} from the sole company, {still_missing} person(s) still without a profile"
    )


def downgrade() -> None:
    # Data-only fill; nothing distinguishes backfilled rows from later ones, so
    # there is nothing safe to reverse.
    pass
