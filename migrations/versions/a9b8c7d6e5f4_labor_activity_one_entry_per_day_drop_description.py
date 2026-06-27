"""labor_activity_one_entry_per_day_drop_description

One labor-activity entry per (project_id, date):
  1. Fold non-empty description into title (title || ' — ' || description).
  2. Merge multi-activity days: concat extra rows' titles (' / ') into the
     earliest row (by created_at), delete extras.
  3. Add unique constraint uq_labor_activities_project_date (project_id, date).
  4. Drop column description.

DOWNGRADE NOTE: re-adds the nullable description column and drops the unique
constraint. The folded/merged content is NOT recoverable — existing title
values will contain the concatenated text.
"""

import sqlalchemy as sa
from alembic import op

revision = "a9b8c7d6e5f4"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Step 0 — Widen title to Text BEFORE folding. The dropped description was
    # Text (API allowed up to 2000 chars) and the merge below concatenates
    # several titles; either can exceed the old VARCHAR(255) and abort the
    # upgrade on real data. Title is now the only free-text field, so Text is
    # the honest type.
    op.alter_column(
        "labor_activities",
        "title",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=False,
    )

    # Step 1 — Fold description into title where description is non-empty.
    op.execute(
        sa.text(
            "UPDATE labor_activities"
            " SET title = title || ' — ' || description"
            " WHERE description IS NOT NULL AND description <> ''"
        )
    )

    # Step 2 — Merge duplicate (project_id, date) rows.
    # For each group with more than one row, keep the row with the earliest
    # created_at, concatenate all titles (ordered by created_at) with ' / ',
    # then delete the non-keeper rows.
    op.execute(
        sa.text(
            """
WITH keepers AS (
    -- Pick the row with the minimum created_at per (project_id, date) group.
    SELECT DISTINCT ON (project_id, date)
        id AS keeper_id,
        project_id,
        date
    FROM labor_activities
    ORDER BY project_id, date, created_at ASC
),
groups_with_duplicates AS (
    -- Only (project_id, date) pairs that actually have >1 row.
    SELECT project_id, date
    FROM labor_activities
    GROUP BY project_id, date
    HAVING COUNT(*) > 1
),
merged_titles AS (
    -- For each duplicate group, build the combined title ordered by created_at.
    SELECT
        k.keeper_id,
        string_agg(la.title, ' / ' ORDER BY la.created_at ASC) AS combined_title
    FROM keepers k
    JOIN groups_with_duplicates g
        ON k.project_id = g.project_id AND k.date = g.date
    JOIN labor_activities la
        ON la.project_id = k.project_id AND la.date = k.date
    GROUP BY k.keeper_id
)
UPDATE labor_activities la
SET title = mt.combined_title
FROM merged_titles mt
WHERE la.id = mt.keeper_id
"""
        )
    )

    op.execute(
        sa.text(
            """
DELETE FROM labor_activities la
WHERE EXISTS (
    SELECT 1
    FROM (
        SELECT DISTINCT ON (project_id, date)
            id AS keeper_id,
            project_id,
            date
        FROM labor_activities
        ORDER BY project_id, date, created_at ASC
    ) k
    WHERE k.project_id = la.project_id
      AND k.date = la.date
      AND k.keeper_id <> la.id
)
"""
        )
    )

    # Step 3 — Add unique constraint.
    op.create_unique_constraint(
        "uq_labor_activities_project_date",
        "labor_activities",
        ["project_id", "date"],
    )

    # Step 4 — Drop description column.
    op.drop_column("labor_activities", "description")


def downgrade() -> None:
    # Re-add nullable description column (folded/merged content is not recoverable).
    op.add_column(
        "labor_activities",
        sa.Column("description", sa.Text(), nullable=True),
    )
    # Drop unique constraint.
    op.drop_constraint(
        "uq_labor_activities_project_date",
        "labor_activities",
        type_="unique",
    )
