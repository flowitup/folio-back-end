"""company_persons + company_member_grants + company scoping (Phase 2 slice A)

Schema for Phase 2 of the roles & permissions redesign (D8 grants, company
person directory, and company scoping of labor roles / billing templates /
persons). This migration lands the SCHEMA + BACKFILLS only — onboarding use
cases, the grants endpoints, and worker-from-person creation are separate
slices built on top of these tables.

1. New tables:
   - `company_persons` — company-scoped profile of a global `Person`
     (rate, labor role, active/pending state). `UNIQUE(company_id, person_id)`
     plus a partial `UNIQUE(company_id, phone_normalized) WHERE
     phone_normalized IS NOT NULL` (a person's global phone is never unique,
     but within one company it must be, so onboarding-by-phone can find a
     single match or report a real conflict).
   - `company_member_grants` — D8 per-user grant/deny rows. A plain
     `UNIQUE(company_id, user_id, permission, project_id)` still allows two
     company-wide rows (`project_id IS NULL`) for the same permission on
     Postgres (NULL is never equal to NULL in a unique constraint), so a
     second partial unique index closes that gap.

2. Column additions:
   - `persons.user_id` (nullable, unique) + `persons.phone_normalized`
     (nullable, plain index) — backfilled by
     `app.infrastructure.database.backfills.persons_phone_normalized`.
   - `companies.default_phone_region` (NOT NULL, default 'FR').
   - `labor_roles.company_id` (nullable FK) + `labor_roles.slug` — backfilled
     by `app.infrastructure.database.backfills.labor_roles_company`;
     `UNIQUE(name)` is replaced by `UNIQUE(company_id, name)` so the same
     role name can exist in two companies.
   - `billing_document_templates.company_id` (nullable FK) — backfilled by
     `app.infrastructure.database.backfills.billing_templates_company`;
     `UNIQUE(user_id, kind, name)` is replaced by
     `UNIQUE(company_id, user_id, kind, name)`.
   - `projects.company_id` FK changed from `ON DELETE SET NULL` to
     `ON DELETE RESTRICT` — a project is a company asset; deleting a company
     that still owns projects must fail loudly, not silently orphan them.
     The column STAYS NULLABLE: the seed DB (and possibly prod) still has a
     few orphaned projects with no resolvable owner company (see migration
     15c1df3fdbfa's printed count) — tightening to NOT NULL is deferred to
     Phase 4 of the roles & permissions redesign, once every environment is
     confirmed clean.

Every backfill prints how many rows it could not resolve (ambiguous or
missing data) so a human can review them; none of them fail the migration.

Revision ID: 2ca24be9e3a8
Revises: 15c1df3fdbfa
Create Date: 2026-09-08 00:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.infrastructure.database.backfills.billing_templates_company import (
    run_backfill as run_billing_template_backfill,
)
from app.infrastructure.database.backfills.labor_roles_company import (
    run_backfill as run_labor_role_backfill,
)
from app.infrastructure.database.backfills.persons_phone_normalized import (
    run_backfill as run_persons_backfill,
)

# revision identifiers, used by Alembic.
revision = "2ca24be9e3a8"
down_revision = "15c1df3fdbfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # -----------------------------------------------------------------
    # 1. company_persons
    # -----------------------------------------------------------------
    op.create_table(
        "company_persons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("persons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "labor_role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("labor_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("default_daily_rate", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("phone_normalized", sa.String(32), nullable=True),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "person_id", name="uq_company_persons_company_person"),
    )
    op.create_index("ix_company_persons_company_id", "company_persons", ["company_id"])
    op.create_index(
        "ix_company_persons_company_phone_unique",
        "company_persons",
        ["company_id", "phone_normalized"],
        unique=True,
        postgresql_where=sa.text("phone_normalized IS NOT NULL"),
    )

    # -----------------------------------------------------------------
    # 2. company_member_grants (D8)
    # -----------------------------------------------------------------
    op.create_table(
        "company_member_grants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("effect", sa.String(8), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "granted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("effect IN ('grant','deny')", name="ck_company_member_grants_effect"),
        sa.UniqueConstraint("company_id", "user_id", "permission", "project_id", name="uq_company_member_grants_scope"),
    )
    op.create_index("ix_company_member_grants_user_company", "company_member_grants", ["user_id", "company_id"])
    op.create_index(
        "ix_company_member_grants_company_wide_unique",
        "company_member_grants",
        ["company_id", "user_id", "permission"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )

    # -----------------------------------------------------------------
    # 3. persons.user_id, persons.phone_normalized
    # -----------------------------------------------------------------
    op.add_column(
        "persons",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_unique_constraint("uq_persons_user_id", "persons", ["user_id"])
    op.add_column("persons", sa.Column("phone_normalized", sa.String(32), nullable=True))
    op.create_index("ix_persons_phone_normalized", "persons", ["phone_normalized"])

    # -----------------------------------------------------------------
    # 4. companies.default_phone_region
    # -----------------------------------------------------------------
    op.add_column(
        "companies",
        sa.Column("default_phone_region", sa.String(2), nullable=False, server_default="FR"),
    )

    # -----------------------------------------------------------------
    # 5. labor_roles.company_id, labor_roles.slug, unique(name) → unique(company_id, name)
    # -----------------------------------------------------------------
    op.add_column(
        "labor_roles",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("labor_roles", sa.Column("slug", sa.String(40), nullable=True))
    op.drop_constraint("labor_roles_name_key", "labor_roles", type_="unique")
    op.create_unique_constraint("uq_labor_roles_company_name", "labor_roles", ["company_id", "name"])

    # -----------------------------------------------------------------
    # 6. billing_document_templates.company_id, unique(user,kind,name) → unique(company,user,kind,name)
    # -----------------------------------------------------------------
    op.add_column(
        "billing_document_templates",
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_billing_document_templates_company_id", "billing_document_templates", ["company_id"])
    op.drop_constraint("uq_billing_template_user_kind_name", "billing_document_templates", type_="unique")
    op.create_unique_constraint(
        "uq_billing_template_company_user_kind_name",
        "billing_document_templates",
        ["company_id", "user_id", "kind", "name"],
    )

    # -----------------------------------------------------------------
    # 7. projects.company_id FK: SET NULL → RESTRICT (column stays nullable)
    #    Constraint name matches the one migration b1c2d3e4f5a6 created it
    #    with (fk_projects_company_id), not the Postgres default naming.
    # -----------------------------------------------------------------
    op.drop_constraint("fk_projects_company_id", "projects", type_="foreignkey")
    op.create_foreign_key(
        "fk_projects_company_id",
        "projects",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # -----------------------------------------------------------------
    # 8. Data backfills — after every column/index exists.
    # -----------------------------------------------------------------
    ambiguous_users, unparseable_phones = run_persons_backfill(conn)
    if ambiguous_users:
        print(
            f"persons.user_id backfill: {len(ambiguous_users)} user(s) map to more than one "
            f"person — left unlinked for ops review: {ambiguous_users}"
        )
    print(f"persons.phone_normalized backfill: {unparseable_phones} phone(s) did not parse — left NULL")

    still_null_roles = run_labor_role_backfill(conn)
    print(f"labor_roles.company_id backfill: {still_null_roles} role(s) still have no company_id")

    still_null_templates = run_billing_template_backfill(conn)
    print(
        f"billing_document_templates.company_id backfill: {still_null_templates} "
        f"template(s) still have no company_id (excluded from company-scoped lists)"
    )


def _has_duplicates(conn, *, table: str, columns: list[str]) -> bool:
    """Return True if `table` has more than one row sharing the same
    `columns` values (M4): recreating a global unique constraint over
    duplicate rows would abort the downgrade outright, taking down the whole
    rollback with it. Checked BEFORE attempting to drop the per-company
    constraint, so a dirty downgrade leaves the safer per-company constraint
    in place instead of erroring out mid-migration."""
    cols = ", ".join(columns)
    result = conn.execute(sa.text(f"SELECT 1 FROM {table} GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1")).fetchone()
    return result is not None


def downgrade() -> None:
    conn = op.get_bind()

    # 7. projects.company_id FK: RESTRICT → SET NULL
    op.drop_constraint("fk_projects_company_id", "projects", type_="foreignkey")
    op.create_foreign_key(
        "fk_projects_company_id",
        "projects",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 6. billing_document_templates: only recreate the global
    # UNIQUE(user_id, kind, name) if no two rows (now possibly in different
    # companies) would collide under it — otherwise keep the per-company
    # constraint and warn, rather than aborting the whole downgrade (M4).
    if _has_duplicates(conn, table="billing_document_templates", columns=["user_id", "kind", "name"]):
        print(
            "billing_document_templates downgrade: duplicate (user_id, kind, name) rows exist across "
            "companies — keeping uq_billing_template_company_user_kind_name instead of the global "
            "constraint. Resolve the duplicates manually before re-attempting this downgrade."
        )
    else:
        op.drop_constraint("uq_billing_template_company_user_kind_name", "billing_document_templates", type_="unique")
        op.create_unique_constraint(
            "uq_billing_template_user_kind_name", "billing_document_templates", ["user_id", "kind", "name"]
        )
    op.drop_index("ix_billing_document_templates_company_id", table_name="billing_document_templates")
    op.drop_column("billing_document_templates", "company_id")

    # 5. labor_roles: same guard for the global UNIQUE(name) (M4) — two
    # different companies legitimately have a role with the same name today.
    if _has_duplicates(conn, table="labor_roles", columns=["name"]):
        print(
            "labor_roles downgrade: duplicate name(s) exist across companies — keeping "
            "uq_labor_roles_company_name instead of the global UNIQUE(name). Resolve the "
            "duplicates manually before re-attempting this downgrade."
        )
    else:
        op.drop_constraint("uq_labor_roles_company_name", "labor_roles", type_="unique")
        op.create_unique_constraint("labor_roles_name_key", "labor_roles", ["name"])
    op.drop_column("labor_roles", "slug")
    op.drop_column("labor_roles", "company_id")

    # 4. companies
    op.drop_column("companies", "default_phone_region")

    # 3. persons
    op.drop_index("ix_persons_phone_normalized", table_name="persons")
    op.drop_column("persons", "phone_normalized")
    op.drop_constraint("uq_persons_user_id", "persons", type_="unique")
    op.drop_column("persons", "user_id")

    # 2. company_member_grants
    op.drop_table("company_member_grants")

    # 1. company_persons
    op.drop_table("company_persons")
