"""add_chiffrage_stores

Shops to visit for a poste's purchases. A poste gets a list rather than a single
field: buying "Lumière" typically means a run across several shops.

Revision ID: e5b2c74a19f3
Revises: c7a4e91b52d0
Create Date: 2026-08-18 22:45:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e5b2c74a19f3"
down_revision = "c7a4e91b52d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chiffrage_stores",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "poste_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Free text rather than a bibliotheque supplier link: a chain has many
        # branches and which one you drive to depends on the chantier.
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_chiffrage_stores_poste_position", "chiffrage_stores", ["poste_id", "position"])


def downgrade():
    op.drop_index("ix_chiffrage_stores_poste_position", table_name="chiffrage_stores")
    op.drop_table("chiffrage_stores")
