"""add billing module

Revision ID: 97e7156ea751
Revises: 20a22df3582d
Create Date: 2026-05-05 17:15:27.317565

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "97e7156ea751"
down_revision = "20a22df3582d"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Enum type objects used ONLY for explicit .create() / .drop() calls.
# create_type=False prevents SQLAlchemy from auto-creating them again when
# these objects are bound to Column definitions inside op.create_table().
# ---------------------------------------------------------------------------

_kind_enum = postgresql.ENUM(
    "devis",
    "facture",
    name="billing_document_kind",
    create_type=False,  # we call .create() explicitly in upgrade()
)

_status_enum = postgresql.ENUM(
    "draft",
    "sent",
    "accepted",
    "rejected",
    "expired",
    "paid",
    "overdue",
    "cancelled",
    name="billing_document_status",
    create_type=False,  # we call .create() explicitly in upgrade()
)


def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # Create enum types first — DO blocks guard against partial reruns
    # (CREATE TYPE ... IF NOT EXISTS is not valid PostgreSQL syntax for enums)
    # ------------------------------------------------------------------
    conn.execute(
        sa.text(
            "DO $$ BEGIN "
            "  CREATE TYPE billing_document_kind AS ENUM ('devis', 'facture'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )
    )
    conn.execute(
        sa.text(
            "DO $$ BEGIN "
            "  CREATE TYPE billing_document_status AS ENUM "
            "  ('draft', 'sent', 'accepted', 'rejected', 'expired', 'paid', 'overdue', 'cancelled'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$"
        )
    )

    # ------------------------------------------------------------------
    # company_profile  (no FK deps other than users)
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

    # ------------------------------------------------------------------
    # billing_documents
    # ------------------------------------------------------------------
    op.create_table(
        "billing_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column("kind", _kind_enum, nullable=False),
        sa.Column("document_number", sa.String(length=32), nullable=False),
        sa.Column("status", _status_enum, nullable=False, server_default="draft"),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("validity_until", sa.Date(), nullable=True),
        sa.Column("payment_due_date", sa.Date(), nullable=True),
        sa.Column("payment_terms", sa.Text(), nullable=True),
        sa.Column("recipient_name", sa.String(length=255), nullable=False),
        sa.Column("recipient_address", sa.Text(), nullable=True),
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
        sa.Column("recipient_siret", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("signature_block_text", sa.Text(), nullable=True),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("issuer_legal_name", sa.String(length=255), nullable=False),
        sa.Column("issuer_address", sa.Text(), nullable=False),
        sa.Column("issuer_siret", sa.String(length=32), nullable=True),
        sa.Column("issuer_tva_number", sa.String(length=32), nullable=True),
        sa.Column("issuer_iban", sa.String(length=64), nullable=True),
        sa.Column("issuer_bic", sa.String(length=32), nullable=True),
        sa.Column("issuer_logo_url", sa.Text(), nullable=True),
        sa.Column("source_devis_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_devis_id"],
            ["billing_documents.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "kind",
            "document_number",
            name="uq_billing_document_user_kind_number",
        ),
        sa.CheckConstraint(
            "kind = 'devis' OR validity_until IS NULL",
            name="ck_billing_doc_validity_until_devis_only",
        ),
        sa.CheckConstraint(
            "kind = 'facture' OR (payment_due_date IS NULL AND payment_terms IS NULL)",
            name="ck_billing_doc_payment_fields_facture_only",
        ),
    )

    # Regular composite index
    op.create_index(
        "ix_billing_documents_user_kind_status",
        "billing_documents",
        ["user_id", "kind", "status"],
        unique=False,
    )
    # Partial unique: one facture per source devis (race prevention on convert)
    op.create_index(
        "uix_billing_documents_source_devis_id",
        "billing_documents",
        ["source_devis_id"],
        unique=True,
        postgresql_where=sa.text("source_devis_id IS NOT NULL"),
    )
    # Partial index: only index project_id rows that are non-NULL
    op.create_index(
        "ix_billing_documents_project_id_partial",
        "billing_documents",
        ["project_id"],
        unique=False,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # billing_document_templates
    # ------------------------------------------------------------------
    op.create_table(
        "billing_document_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", _kind_enum, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("terms", sa.Text(), nullable=True),
        sa.Column("default_vat_rate", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "kind",
            "name",
            name="uq_billing_template_user_kind_name",
        ),
    )
    op.create_index(
        "ix_billing_document_templates_user_kind",
        "billing_document_templates",
        ["user_id", "kind"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # billing_number_counters
    # ------------------------------------------------------------------
    op.create_table(
        "billing_number_counters",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", _kind_enum, nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "kind", "year"),
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order
    op.drop_table("billing_number_counters")
    op.drop_index(
        "ix_billing_document_templates_user_kind",
        table_name="billing_document_templates",
    )
    op.drop_table("billing_document_templates")
    op.drop_index(
        "ix_billing_documents_project_id_partial",
        table_name="billing_documents",
    )
    op.drop_index(
        "uix_billing_documents_source_devis_id",
        table_name="billing_documents",
    )
    op.drop_index(
        "ix_billing_documents_user_kind_status",
        table_name="billing_documents",
    )
    op.drop_table("billing_documents")
    op.drop_table("company_profile")

    # Drop enum types last
    conn = op.get_bind()
    conn.execute(sa.text("DROP TYPE IF EXISTS billing_document_status"))
    conn.execute(sa.text("DROP TYPE IF EXISTS billing_document_kind"))
