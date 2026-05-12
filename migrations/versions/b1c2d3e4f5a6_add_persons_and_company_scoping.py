"""add persons table, workers.person_id, and projects.company_id

Revision ID: b1c2d3e4f5a6
Revises: a3f7b8c9d0e1
Create Date: 2026-05-13 00:00:00.000000

SUMMARY
-------
Three additive schema changes that lay the groundwork for cross-project
labor identity tracking and company-scoped project privacy:

1. Creates ``persons`` table — global identity entity decoupled from
   Project and Company. Same Person can have Worker rows in projects
   belonging to different companies (multi-company support).

2. Adds nullable ``workers.person_id`` FK to ``persons``. Backfill is
   deferred to a dedicated script (Phase 1c). Column tightens to
   NOT NULL in a follow-up release once 100% populated.

3. Adds nullable ``projects.company_id`` FK to ``companies``, with
   inline backfill: project.company_id := owner's primary company
   (mirrors how billing chooses the issuer). Column tightens to
   NOT NULL in a follow-up release.

All three changes are ADDITIVE. No columns are dropped. No production
read paths exist for the new columns yet — those land in Phase 1b.

ONDELETE BEHAVIOR
-----------------
* persons.created_by_user_id → users.id ON DELETE RESTRICT
    (audit trail; user delete blocked while persons reference them)
* workers.person_id → persons.id ON DELETE RESTRICT
    (a Person referenced by any Worker cannot be deleted; merge tool
    moves workers off the source before deletion)
* projects.company_id → companies.id ON DELETE SET NULL
    (matches billing_documents pattern; project becomes orphaned and
    requires manual reassignment by admin)

BACKFILL EDGE CASES
-------------------
projects.company_id backfill uses owner's ``is_primary=TRUE`` row in
``user_company_access``. Rows where the owner has no primary company
(rare; legacy users from before the companies module) are left NULL —
admins resolve manually via project settings once the FE picker ships.

DOWNGRADE
---------
Drops projects.company_id (data lost), then workers.person_id (no data
to lose pre-backfill), then persons table. Safe so long as no read
paths consume these columns yet.

CONTEXT
-------
Part of plan: 260512-2341-labor-calendar-and-bulk-log → phase-01.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "a3f7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create persons table
    # ------------------------------------------------------------------
    # normalized_name = lower(trim(name)) — populated by application
    # layer on insert/update. Used for case-insensitive search + dedup
    # hints. Phone is nullable; uniqueness is intentionally NOT enforced
    # because two people may legitimately share a phone (family device).
    # Deduplication is human-reviewed via the merge tool (Phase 1c).
    op.create_table(
        "persons",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_persons_normalized_name",
        "persons",
        ["normalized_name"],
        unique=False,
    )
    op.create_index(
        "ix_persons_created_by",
        "persons",
        ["created_by_user_id"],
        unique=False,
    )
    # Partial index on phone — most lookups filter by exact phone for
    # cross-project identity hints, but NULLs are common so we skip them.
    op.create_index(
        "ix_persons_phone",
        "persons",
        ["phone"],
        unique=False,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 2. Add workers.person_id (nullable; backfilled in Phase 1c script)
    # ------------------------------------------------------------------
    op.add_column(
        "workers",
        sa.Column("person_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_workers_person_id",
        "workers",
        "persons",
        ["person_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_workers_person_id",
        "workers",
        ["person_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # 3. Add projects.company_id (nullable) + inline backfill from
    #    owner's primary company access
    # ------------------------------------------------------------------
    op.add_column(
        "projects",
        sa.Column("company_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_company_id",
        "projects",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_projects_company_id",
        "projects",
        ["company_id"],
        unique=False,
    )

    # Inline backfill: set projects.company_id to the owner's primary
    # company. Orphans (owner has no is_primary=TRUE row) stay NULL —
    # admin resolves via project settings UI in a later phase.
    op.execute(
        sa.text(
            """
            UPDATE projects p
            SET company_id = uca.company_id
            FROM user_company_access uca
            WHERE uca.user_id = p.owner_id
              AND uca.is_primary = TRUE
              AND p.company_id IS NULL
            """
        )
    )


def downgrade() -> None:
    # Reverse order: drop projects.company_id first (independent),
    # then workers.person_id, then persons table.
    op.drop_index("ix_projects_company_id", table_name="projects")
    op.drop_constraint("fk_projects_company_id", "projects", type_="foreignkey")
    op.drop_column("projects", "company_id")

    op.drop_index("ix_workers_person_id", table_name="workers")
    op.drop_constraint("fk_workers_person_id", "workers", type_="foreignkey")
    op.drop_column("workers", "person_id")

    op.drop_index("ix_persons_phone", table_name="persons")
    op.drop_index("ix_persons_created_by", table_name="persons")
    op.drop_index("ix_persons_normalized_name", table_name="persons")
    op.drop_table("persons")
