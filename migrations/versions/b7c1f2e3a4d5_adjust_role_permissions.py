"""adjust_role_permissions

Data migration (idempotent + reversible):
  - grant the `manager` role `project:invite` and `bibliotheque:manage`
  - give the `member` role real read access (`project:read`, `user:read`)
  - drop the unused `user:create` / `user:update` / `user:delete` permissions
    (no role references them except the `*:*` wildcard, so admin is unaffected;
     role_permissions rows cascade-delete via the FK)

Revision ID: b7c1f2e3a4d5
Revises: a1b2c4d8f3e9
Create Date: 2026-06-03 00:30:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b7c1f2e3a4d5"
down_revision = "a1b2c4d8f3e9"
branch_labels = None
depends_on = None


def upgrade():
    # Ensure the permissions we grant exist (no-op if already seeded).
    op.execute(
        """
        INSERT INTO permissions (id, name, resource, action)
        VALUES (gen_random_uuid(), 'project:invite', 'project', 'invite')
        ON CONFLICT (name) DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO permissions (id, name, resource, action)
        VALUES (gen_random_uuid(), 'bibliotheque:manage', 'bibliotheque', 'manage')
        ON CONFLICT (name) DO NOTHING;
        """
    )

    # manager: + project:invite, bibliotheque:manage
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.name = 'manager' AND p.name IN ('project:invite', 'bibliotheque:manage')
        ON CONFLICT DO NOTHING;
        """
    )

    # member: was a no-op role — give it read access so it is a real viewer role.
    op.execute(
        """
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r, permissions p
        WHERE r.name = 'member' AND p.name IN ('project:read', 'user:read')
        ON CONFLICT DO NOTHING;
        """
    )

    # Drop unused user-CRUD permissions (admin keeps everything via *:*).
    # role_permissions rows referencing them cascade-delete via the FK.
    op.execute("DELETE FROM permissions WHERE name IN ('user:create', 'user:update', 'user:delete');")


def downgrade():
    # Re-create the dropped permissions (unassigned, matching their original catalog form).
    op.execute(
        """
        INSERT INTO permissions (id, name, resource, action) VALUES
            (gen_random_uuid(), 'user:create', 'user', 'create'),
            (gen_random_uuid(), 'user:update', 'user', 'update'),
            (gen_random_uuid(), 'user:delete', 'user', 'delete')
        ON CONFLICT (name) DO NOTHING;
        """
    )

    # Revoke the grants added in upgrade (leave the project:invite / bibliotheque:manage
    # permission rows in place — they predate this migration and belong to the catalog).
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id = (SELECT id FROM roles WHERE name = 'manager')
          AND permission_id IN (
            SELECT id FROM permissions WHERE name IN ('project:invite', 'bibliotheque:manage')
          );
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE role_id = (SELECT id FROM roles WHERE name = 'member')
          AND permission_id IN (
            SELECT id FROM permissions WHERE name IN ('project:read', 'user:read')
          );
        """
    )
