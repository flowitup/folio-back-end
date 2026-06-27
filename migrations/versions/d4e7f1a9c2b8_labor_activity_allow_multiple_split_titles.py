"""labor_activity_allow_multiple_split_titles

Restore multiple labor-activity entries per (project_id, date):
  1. Drop the unique constraint uq_labor_activities_project_date.
  2. Split slash-joined titles back into separate rows. A prior migration
     merged a day's activities into the earliest row, concatenating their
     titles with ' / '. Here each ' / '-separated segment becomes its own row,
     preserving project_id, date and created_by; created_at is nudged by a
     microsecond per segment so the original left-to-right order is stable.

NOTE: the split keys on the exact ' / ' separator the merge produced. A title
that legitimately contains ' / ' will be split too — this is inherent to the
text-merge that the previous migration performed and is accepted.

DOWNGRADE: re-merge each day's rows into the earliest (titles joined by ' / '),
delete the extras, and re-add the unique constraint.
"""

from datetime import timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "d4e7f1a9c2b8"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None

_SEPARATOR = " / "


def upgrade() -> None:
    # Step 1 — Drop the unique constraint so a day can hold many activities.
    op.drop_constraint(
        "uq_labor_activities_project_date",
        "labor_activities",
        type_="unique",
    )

    # Step 2 — Split slash-joined titles into separate rows.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, project_id, date, title, created_by, created_at"
            " FROM labor_activities WHERE title LIKE :pattern"
        ),
        {"pattern": "%" + _SEPARATOR + "%"},
    ).fetchall()

    # Explicit ::uuid casts keep the bound string/None values type-correct
    # regardless of how the driver adapts Python UUID objects.
    insert_stmt = sa.text(
        "INSERT INTO labor_activities"
        " (id, project_id, date, title, created_by, created_at, updated_at)"
        " VALUES (CAST(:id AS uuid), CAST(:project_id AS uuid), :date, :title,"
        " CAST(:created_by AS uuid), :created_at, :updated_at)"
    )
    update_stmt = sa.text("UPDATE labor_activities SET title = :title WHERE id = CAST(:id AS uuid)")

    for row in rows:
        segments = [seg.strip() for seg in row.title.split(_SEPARATOR) if seg.strip()]
        if len(segments) <= 1:
            # Nothing to split (e.g. title was just a bare separator) — leave as is.
            continue

        # Keep the first segment on the existing row.
        conn.execute(update_stmt, {"title": segments[0], "id": str(row.id)})

        # Insert the remaining segments as new rows, preserving order.
        created_by = str(row.created_by) if row.created_by is not None else None
        for offset, segment in enumerate(segments[1:], start=1):
            created_at = row.created_at + timedelta(microseconds=offset)
            conn.execute(
                insert_stmt,
                {
                    "id": str(uuid4()),
                    "project_id": str(row.project_id),
                    "date": row.date,
                    "title": segment,
                    "created_by": created_by,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            )


def downgrade() -> None:
    # Re-merge each (project_id, date) group's rows into the earliest one,
    # concatenating titles with ' / ' ordered by created_at, then delete extras.
    op.execute(
        sa.text(
            """
WITH keepers AS (
    SELECT DISTINCT ON (project_id, date)
        id AS keeper_id,
        project_id,
        date
    FROM labor_activities
    ORDER BY project_id, date, created_at ASC
),
groups_with_duplicates AS (
    SELECT project_id, date
    FROM labor_activities
    GROUP BY project_id, date
    HAVING COUNT(*) > 1
),
merged_titles AS (
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

    op.create_unique_constraint(
        "uq_labor_activities_project_date",
        "labor_activities",
        ["project_id", "date"],
    )
