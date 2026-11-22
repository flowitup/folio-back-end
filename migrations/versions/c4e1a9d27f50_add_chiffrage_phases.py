"""add_chiffrage_phases

The stages of works (giai đoạn) of each poste — for the electricity: conduits
and boxes, then the panel, then the switches. Each poste orders its own; its
articles point into them. The column is nullable so existing lines stay valid
and read as "no stage yet".

Revision ID: c4e1a9d27f50
Revises: a7c3e91d5b24
Create Date: 2026-11-22 01:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e1a9d27f50"
down_revision = "a7c3e91d5b24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "chiffrage_phases",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "poste_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("chiffrage_postes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("poste_id", "name", name="uq_chiffrage_phases_poste_name"),
    )
    op.create_index("ix_chiffrage_phases_poste_position", "chiffrage_phases", ["poste_id", "position"])
    op.create_index("ix_chiffrage_phases_project_id", "chiffrage_phases", ["project_id"])

    # SET NULL, not CASCADE: deleting a stage must not delete the items planned
    # for it — they resurface without a stage.
    op.add_column("chiffrage_articles", sa.Column("phase_id", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_chiffrage_articles_phase_id",
        "chiffrage_articles",
        "chiffrage_phases",
        ["phase_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chiffrage_articles_phase_id", "chiffrage_articles", ["phase_id"])


def downgrade():
    op.drop_index("ix_chiffrage_articles_phase_id", table_name="chiffrage_articles")
    op.drop_constraint("fk_chiffrage_articles_phase_id", "chiffrage_articles", type_="foreignkey")
    op.drop_column("chiffrage_articles", "phase_id")
    op.drop_index("ix_chiffrage_phases_project_id", table_name="chiffrage_phases")
    op.drop_index("ix_chiffrage_phases_poste_position", table_name="chiffrage_phases")
    op.drop_table("chiffrage_phases")
