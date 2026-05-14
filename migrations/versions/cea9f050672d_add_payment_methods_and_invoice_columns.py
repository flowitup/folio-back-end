"""add payment_methods and invoice columns

Revision ID: cea9f050672d
Revises: b8e2d1c47a90
Create Date: 2026-05-15 00:17:26.457994

Creates the ``payment_methods`` table and adds ``payment_method_id`` /
``payment_method_label`` columns to the ``invoices`` table.

SQLite compatibility note
--------------------------
``postgresql_where`` clauses on ``op.create_index`` are silently ignored by
SQLite (Alembic emits a standard index without the WHERE predicate). This
means the partial-unique constraint is only enforced on PostgreSQL. SQLite
test sessions will accept duplicate (company_id, lower(label)) combinations
for inactive rows, which is acceptable because those tests do not exercise
the soft-delete uniqueness guarantee.

The functional part of the index (``lower(label)``) is expressed as raw SQL
via ``op.execute`` rather than via ``op.create_index`` column expressions
because Alembic's ``create_index`` does not support arbitrary SQL expressions
in a SQLite-compatible way. The ``op.execute`` call is guarded with a
``dialect.name == 'postgresql'`` check so SQLite test runs are unaffected.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# revision identifiers, used by Alembic.
revision = "cea9f050672d"
down_revision = "b8e2d1c47a90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create payment_methods table
    # ------------------------------------------------------------------
    op.create_table(
        "payment_methods",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "company_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column(
            "is_builtin",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_by",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )

    # ------------------------------------------------------------------
    # 2. Indexes on payment_methods
    # ------------------------------------------------------------------

    # Composite covering index for the most common query pattern:
    # "all active methods for a company" — also covers company_id alone.
    op.create_index(
        "ix_payment_methods_company_active",
        "payment_methods",
        ["company_id", "is_active"],
        unique=False,
    )

    # Partial unique functional index: one label per company (case-insensitive),
    # active rows only. Expressed as raw DDL because Alembic's create_index
    # cannot represent functional expressions portably. Guarded to PG only so
    # SQLite-backed test sessions skip it cleanly.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                "CREATE UNIQUE INDEX ux_payment_methods_company_label_active "
                "ON payment_methods (company_id, lower(label)) "
                "WHERE is_active = true"
            )
        )

    # ------------------------------------------------------------------
    # 3. Add payment method columns to invoices
    # ------------------------------------------------------------------
    op.add_column(
        "invoices",
        sa.Column(
            "payment_method_id",
            PG_UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "invoices",
        sa.Column("payment_method_label", sa.String(120), nullable=True),
    )
    op.create_foreign_key(
        "fk_invoices_payment_method_id",
        "invoices",
        "payment_methods",
        ["payment_method_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Sparse index: only rows that have a method set benefit from this index.
    op.create_index(
        "ix_invoices_payment_method_id",
        "invoices",
        ["payment_method_id"],
        unique=False,
        postgresql_where=sa.text("payment_method_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 4. Seed builtin methods for every existing company
    #    Two statements:
    #      a) Cash — always
    #      b) legal_name — when not NULL and not equal to 'Cash'
    #    Both are idempotent via ON CONFLICT DO NOTHING against the partial
    #    unique index (only runs on PostgreSQL where the index exists).
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                INSERT INTO payment_methods
                    (id, company_id, label, is_builtin, is_active, created_by, created_at, updated_at)
                SELECT
                    gen_random_uuid(), c.id, 'Cash', true, true,
                    c.created_by, now(), now()
                FROM companies c
                ON CONFLICT DO NOTHING;

                INSERT INTO payment_methods
                    (id, company_id, label, is_builtin, is_active, created_by, created_at, updated_at)
                SELECT
                    gen_random_uuid(), c.id, c.legal_name, true, true,
                    c.created_by, now(), now()
                FROM companies c
                WHERE c.legal_name IS NOT NULL
                  AND lower(c.legal_name) <> 'cash'
                ON CONFLICT DO NOTHING;
                """
            )
        )


def downgrade() -> None:
    # Drop invoice foreign key + columns first (referencing payment_methods)
    op.drop_index("ix_invoices_payment_method_id", table_name="invoices")
    op.drop_constraint("fk_invoices_payment_method_id", "invoices", type_="foreignkey")
    op.drop_column("invoices", "payment_method_label")
    op.drop_column("invoices", "payment_method_id")

    # Drop payment_methods indexes then the table itself
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("DROP INDEX IF EXISTS ux_payment_methods_company_label_active"))

    op.drop_index("ix_payment_methods_company_active", table_name="payment_methods")
    op.drop_table("payment_methods")
