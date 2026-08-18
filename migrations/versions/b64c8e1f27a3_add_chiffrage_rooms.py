"""add_chiffrage_rooms

The chantier's rooms, declared once per project and reused by every poste.
Articles point into them; the column is nullable so lines created before this
migration stay valid and simply read as unassigned.

Revision ID: b64c8e1f27a3
Revises: f2d7b53a8c14
Create Date: 2026-08-19 01:40:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b64c8e1f27a3"
down_revision = "f2d7b53a8c14"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chiffrage_rooms",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("project_id", "name", name="uq_chiffrage_rooms_project_name"),
    )
    op.create_index("ix_chiffrage_rooms_project_position", "chiffrage_rooms", ["project_id", "position"])

    # SET NULL, not CASCADE: deleting a room must not delete the items that were
    # planned for it — they resurface as unassigned.
    op.add_column("chiffrage_articles", sa.Column("room_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_chiffrage_articles_room_id",
        "chiffrage_articles",
        "chiffrage_rooms",
        ["room_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chiffrage_articles_room_id", "chiffrage_articles", ["room_id"])


def downgrade():
    op.drop_index("ix_chiffrage_articles_room_id", table_name="chiffrage_articles")
    op.drop_constraint("fk_chiffrage_articles_room_id", "chiffrage_articles", type_="foreignkey")
    op.drop_column("chiffrage_articles", "room_id")
    op.drop_index("ix_chiffrage_rooms_project_position", table_name="chiffrage_rooms")
    op.drop_table("chiffrage_rooms")
