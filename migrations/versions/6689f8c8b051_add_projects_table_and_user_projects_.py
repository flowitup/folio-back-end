"""add projects table and user_projects association

Revision ID: 6689f8c8b051
Revises: 7f6bfdbaee86
Create Date: 2026-01-24 01:54:26.728494

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6689f8c8b051'
down_revision = '7f6bfdbaee86'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('projects',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=True),
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['owner_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_projects_owner_id', 'projects', ['owner_id'], unique=False)

    op.create_table('user_projects',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'project_id')
    )


def downgrade():
    op.drop_table('user_projects')
    op.drop_index('ix_projects_owner_id', table_name='projects')
    op.drop_table('projects')
