"""Reset photo capture dates the API now refuses to their upload time.

A capture date before 1900 (one normalised to UTC before year 1 cannot even be
read back by the driver) made the project's photo list, the thumbnail and the
delete 500 for every photo of the project. The API now refuses dates before
1900 or more than a day ahead; this moves rows stored earlier back to the time
they were uploaded. Data only: no schema change.

Revision ID: e4b7c2d9a615
Revises: b8d2f6a41c93
Create Date: 2026-10-10
"""

from alembic import op

revision = "e4b7c2d9a615"
down_revision = "b8d2f6a41c93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE project_photos
           SET captured_at = created_at
         WHERE captured_at < TIMESTAMPTZ '1900-01-01 00:00:00+00'
            OR captured_at > now() + INTERVAL '1 day'
        """
    )


def downgrade() -> None:
    # The refused dates are not kept: there is nothing to restore.
    pass
