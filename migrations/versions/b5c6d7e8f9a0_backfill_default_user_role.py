"""backfill default user role for users missing global roles

Revision ID: b5c6d7e8f9a0
Revises: a4f5b6c7d8e9
Create Date: 2026-05-21 00:30:00.000000

"""

from alembic import op
from sqlalchemy import text

revision = "b5c6d7e8f9a0"
down_revision = "a4f5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(
        text(
            """
            INSERT INTO user_roles (user_id, role_id)
            SELECT u.id, r.id
            FROM users u
            CROSS JOIN roles r
            WHERE r.name = 'user'
              AND u.is_active = true
              AND u.id NOT IN (SELECT user_id FROM user_roles)
            ON CONFLICT DO NOTHING
            """
        )
    )


def downgrade():
    pass
