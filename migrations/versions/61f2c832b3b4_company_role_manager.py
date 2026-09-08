"""add 'manager' to user_company_access.role CHECK constraint

Adds the third company role introduced by the roles/permissions redesign
(Phase 1): a company member can now be admin | manager | member, not just
admin | member. No data is migrated by this revision — existing rows keep
their current role string ('admin' or 'member'); the application layer
(SetMemberRoleUseCase) is responsible for ever writing 'manager'.

Revision ID: 61f2c832b3b4
Revises: e9f0a1b2c3d4
Create Date: 2026-09-08 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "61f2c832b3b4"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite has no native ALTER CONSTRAINT; op.create_check_constraint /
    # drop_constraint go through batch mode there (render_as_batch=True is
    # set on the Migrate() instance in app/__init__.py), rebuilding the table.
    # On Postgres this is a plain DROP + ADD CONSTRAINT.
    op.drop_constraint("ck_user_company_access_role", "user_company_access", type_="check")
    op.create_check_constraint(
        "ck_user_company_access_role",
        "user_company_access",
        "role IN ('admin','manager','member')",
    )


def downgrade() -> None:
    # Reversing requires no 'manager' rows exist — the deploy runbook checks
    # this before a rollback; a CHECK violation here surfaces that loudly
    # rather than silently corrupting data.
    op.drop_constraint("ck_user_company_access_role", "user_company_access", type_="check")
    op.create_check_constraint(
        "ck_user_company_access_role",
        "user_company_access",
        "role IN ('admin','member')",
    )
