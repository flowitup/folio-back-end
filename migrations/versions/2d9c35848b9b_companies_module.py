"""companies module

Revision ID: 2d9c35848b9b
Revises: 97e7156ea751
Create Date: 2026-05-06 01:23:16.239605

SUMMARY
-------
Creates 3 new tables (companies, user_company_access, company_invite_tokens),
modifies 2 existing tables (billing_documents adds company_id; billing_number_counters
rekeyed from user_id to company_id), drops 1 table (company_profile).

Migrates all existing company_profile rows into companies + user_company_access.
Backfills billing_documents.company_id and rekeyes billing_number_counters to
use company_id instead of user_id.

DOWNGRADE LOSS WARNING
----------------------
downgrade() reconstructs company_profile using only the "primary" company per
user (the is_primary=TRUE row in user_company_access). Any user who attached
additional (non-primary) companies after migration will lose those secondary
attachments on downgrade — they are not recoverable from the recreated
company_profile table. This loss is documented here and is by design:
company_profile was a 1-user:1-company model; the multi-company model cannot
round-trip through it without data loss.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql  # noqa: F401 — kept for partial index syntax


# revision identifiers, used by Alembic.
revision = "2d9c35848b9b"
down_revision = "97e7156ea751"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 0. pgcrypto extension — required for gen_random_uuid()
    # ------------------------------------------------------------------
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    # ------------------------------------------------------------------
    # 1. Create companies table
    # ------------------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("siret", sa.String(length=32), nullable=True),
        sa.Column("tva_number", sa.String(length=32), nullable=True),
        sa.Column("iban", sa.String(length=64), nullable=True),
        sa.Column("bic", sa.String(length=32), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("default_payment_terms", sa.Text(), nullable=True),
        sa.Column("prefix_override", sa.String(length=8), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "prefix_override IS NULL OR prefix_override ~ '^[A-Z0-9]{1,8}$'",
            name="ck_companies_prefix_override_format",
        ),
    )
    op.create_index("ix_companies_legal_name", "companies", ["legal_name"], unique=False)
    op.create_index("ix_companies_created_by", "companies", ["created_by"], unique=False)

    # ------------------------------------------------------------------
    # 2. Create user_company_access table
    # ------------------------------------------------------------------
    op.create_table(
        "user_company_access",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "company_id"),
    )
    op.create_index(
        "ix_user_company_access_company_id",
        "user_company_access",
        ["company_id"],
        unique=False,
    )
    # Partial unique: at most one primary company per user
    op.create_index(
        "uix_user_company_access_primary_per_user",
        "user_company_access",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_primary = TRUE"),
    )

    # ------------------------------------------------------------------
    # 3. Create company_invite_tokens table
    # ------------------------------------------------------------------
    op.create_table(
        "company_invite_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["redeemed_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_invite_tokens_company_id",
        "company_invite_tokens",
        ["company_id"],
        unique=False,
    )
    # Partial unique: only one active (unredeemed) token per company
    op.create_index(
        "uix_company_invite_tokens_active_per_company",
        "company_invite_tokens",
        ["company_id"],
        unique=True,
        postgresql_where=sa.text("redeemed_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # 4. Add billing_documents.company_id (nullable FK → companies)
    # ------------------------------------------------------------------
    op.add_column(
        "billing_documents",
        sa.Column("company_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "billing_documents_company_id_fkey",
        "billing_documents",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_billing_documents_company_id",
        "billing_documents",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # 5. Data backfill — migrate company_profile → companies + access
    # ------------------------------------------------------------------
    conn.execute(
        sa.text(
            """
            INSERT INTO companies (
                id, legal_name, address, siret, tva_number, iban, bic,
                logo_url, default_payment_terms, prefix_override,
                created_by, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                legal_name, address, siret, tva_number, iban, bic,
                logo_url, default_payment_terms, prefix_override,
                user_id, created_at, updated_at
            FROM company_profile
            """
        )
    )

    # Build mapping temp table: company_profile.user_id → new companies.id
    # (join on created_by = user_id set above — safe because one-to-one at this point)
    conn.execute(
        sa.text(
            """
            CREATE TEMP TABLE _migrated_companies_map AS
            SELECT cp.user_id AS user_id, c.id AS company_id
            FROM company_profile cp
            JOIN companies c ON c.created_by = cp.user_id
            """
        )
    )

    # Each user gets one primary access row for their migrated company
    conn.execute(
        sa.text(
            """
            INSERT INTO user_company_access (user_id, company_id, is_primary, attached_at)
            SELECT user_id, company_id, TRUE, NOW()
            FROM _migrated_companies_map
            """
        )
    )

    # ------------------------------------------------------------------
    # 6. Re-key billing_number_counters: user_id → company_id
    # ------------------------------------------------------------------
    conn.execute(sa.text("ALTER TABLE billing_number_counters ADD COLUMN company_id UUID"))
    conn.execute(
        sa.text(
            """
            UPDATE billing_number_counters c
            SET company_id = m.company_id
            FROM _migrated_companies_map m
            WHERE c.user_id = m.user_id
            """
        )
    )
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP CONSTRAINT billing_number_counters_pkey"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP CONSTRAINT billing_number_counters_user_id_fkey"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP COLUMN user_id"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters ADD PRIMARY KEY (company_id, kind, year)"))
    conn.execute(
        sa.text(
            """
            ALTER TABLE billing_number_counters
            ADD CONSTRAINT billing_number_counters_company_id_fkey
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
            """
        )
    )
    # Mark NOT NULL after all rows updated (safe: every counter row had a user_id in mapping)
    conn.execute(sa.text("ALTER TABLE billing_number_counters ALTER COLUMN company_id SET NOT NULL"))

    # ------------------------------------------------------------------
    # 7. Backfill billing_documents.company_id for existing rows
    # ------------------------------------------------------------------
    conn.execute(
        sa.text(
            """
            UPDATE billing_documents bd
            SET company_id = m.company_id
            FROM _migrated_companies_map m
            WHERE bd.user_id = m.user_id
            """
        )
    )

    # ------------------------------------------------------------------
    # 8. DROP TABLE company_profile  (MUST be last DDL step)
    # ------------------------------------------------------------------
    op.drop_table("company_profile")


def downgrade() -> None:
    """Reverse the companies module migration.

    LOSS WARNING: Users with multiple attached companies after migration will
    lose all but their primary company's data on downgrade. Only the row where
    is_primary=TRUE in user_company_access is used to reconstruct company_profile.
    Secondary attachments are permanently lost on downgrade — this is by design.
    """
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Recreate company_profile from the primary company per user
    # ------------------------------------------------------------------
    op.create_table(
        "company_profile",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("siret", sa.String(length=32), nullable=True),
        sa.Column("tva_number", sa.String(length=32), nullable=True),
        sa.Column("iban", sa.String(length=64), nullable=True),
        sa.Column("bic", sa.String(length=32), nullable=True),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("default_payment_terms", sa.Text(), nullable=True),
        sa.Column("prefix_override", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("user_id", name="uq_company_profile_user_id"),
    )

    # Restore company_profile rows from primary companies only
    conn.execute(
        sa.text(
            """
            INSERT INTO company_profile (
                user_id, legal_name, address, siret, tva_number, iban, bic,
                logo_url, default_payment_terms, prefix_override,
                created_at, updated_at
            )
            SELECT
                uca.user_id,
                c.legal_name, c.address, c.siret, c.tva_number, c.iban, c.bic,
                c.logo_url, c.default_payment_terms, c.prefix_override,
                c.created_at, c.updated_at
            FROM user_company_access uca
            JOIN companies c ON c.id = uca.company_id
            WHERE uca.is_primary = TRUE
            """
        )
    )

    # Build reverse mapping temp table
    conn.execute(
        sa.text(
            """
            CREATE TEMP TABLE _downgrade_companies_map AS
            SELECT uca.user_id, c.id AS company_id
            FROM user_company_access uca
            JOIN companies c ON c.id = uca.company_id
            WHERE uca.is_primary = TRUE
            """
        )
    )

    # ------------------------------------------------------------------
    # Restore billing_number_counters: company_id → user_id
    # ------------------------------------------------------------------
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP CONSTRAINT billing_number_counters_pkey"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP CONSTRAINT billing_number_counters_company_id_fkey"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters ADD COLUMN user_id UUID"))
    conn.execute(
        sa.text(
            """
            UPDATE billing_number_counters c
            SET user_id = m.user_id
            FROM _downgrade_companies_map m
            WHERE c.company_id = m.company_id
            """
        )
    )
    conn.execute(sa.text("ALTER TABLE billing_number_counters DROP COLUMN company_id"))
    conn.execute(sa.text("ALTER TABLE billing_number_counters ADD PRIMARY KEY (user_id, kind, year)"))
    conn.execute(
        sa.text(
            """
            ALTER TABLE billing_number_counters
            ADD CONSTRAINT billing_number_counters_user_id_fkey
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            """
        )
    )

    # ------------------------------------------------------------------
    # Nullify billing_documents.company_id, drop FK + index + column
    # ------------------------------------------------------------------
    conn.execute(sa.text("UPDATE billing_documents SET company_id = NULL"))
    op.drop_index("ix_billing_documents_company_id", table_name="billing_documents")
    op.drop_constraint("billing_documents_company_id_fkey", "billing_documents", type_="foreignkey")
    op.drop_column("billing_documents", "company_id")

    # ------------------------------------------------------------------
    # Drop new tables (reverse dependency order)
    # ------------------------------------------------------------------
    op.drop_index(
        "uix_company_invite_tokens_active_per_company",
        table_name="company_invite_tokens",
    )
    op.drop_index("ix_company_invite_tokens_company_id", table_name="company_invite_tokens")
    op.drop_table("company_invite_tokens")

    op.drop_index(
        "uix_user_company_access_primary_per_user",
        table_name="user_company_access",
    )
    op.drop_index("ix_user_company_access_company_id", table_name="user_company_access")
    op.drop_table("user_company_access")

    op.drop_index("ix_companies_created_by", table_name="companies")
    op.drop_index("ix_companies_legal_name", table_name="companies")
    op.drop_table("companies")
