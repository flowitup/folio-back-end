"""Postgres-only: Alembic round-trip for migration c2b8f1a0d743 (drop legacy roles).

Covers:
  - the upgrade drops `invitations.role_id`, `user_projects.role_id`, then
    `user_roles`, `role_permissions`, `roles` and `permissions`, in an order
    that never leaves a dangling foreign key — a wrong order fails on the FK,
    so a green run IS the order assertion;
  - a pending invitation survives the drop and stays usable;
  - `projects.company_id` becomes NOT NULL, and the upgrade aborts (naming the
    offending project) when one still has none;
  - the downgrade recreates the four tables and both columns, empty.

Skipped unless TEST_DATABASE_URL (or DATABASE_URL) points at a real Postgres —
same pattern as tests/migrations/test_ops_flag_migration.py.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.requires_postgres

_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "sqlite:///:memory:")
if "postgresql" not in _DB_URL:
    pytest.skip("Postgres-only: migration test requires real Postgres", allow_module_level=True)

_PREVIOUS = "9a4c1e7b2d05"
_TARGET = "c2b8f1a0d743"


@pytest.fixture(scope="module")
def alembic_cfg():
    import pathlib

    from alembic.config import Config

    migrations_dir = pathlib.Path(__file__).parents[2] / "migrations"
    cfg = Config(str(migrations_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", _DB_URL)
    return cfg


@pytest.fixture(scope="module")
def migration_app():
    from app import create_app
    from config import TestingConfig

    class _PostgresMigrationConfig(TestingConfig):
        DATABASE_URL = _DB_URL

    return create_app(_PostgresMigrationConfig)


@pytest.fixture(scope="module")
def pg_engine():
    from sqlalchemy import create_engine

    engine = create_engine(_DB_URL, echo=False)
    yield engine
    engine.dispose()


def _run(app, fn, *args):
    with app.app_context():
        return fn(*args)


def _table_exists(conn, table_name: str) -> bool:
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = :tbl"),
            {"tbl": table_name},
        ).scalar()
        > 0
    )


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM information_schema.columns WHERE table_name = :tbl AND column_name = :col"),
            {"tbl": table_name, "col": column_name},
        ).scalar()
        == 1
    )


def _company_id_is_nullable(conn) -> bool:
    return (
        conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'projects' AND column_name = 'company_id'"
            )
        ).scalar()
        == "YES"
    )


def _seed_legacy_state(conn) -> dict:
    """A user with a legacy role, assigned to a project, plus a pending invitation."""
    ids = {k: uuid4() for k in ("user", "role", "perm", "company", "project", "invitation")}
    conn.execute(
        text("INSERT INTO users (id, email, password_hash, is_active) VALUES (:id, :email, 'x', TRUE)"),
        {"id": ids["user"], "email": f"drop-{ids['user'].hex[:8]}@migration-test.com"},
    )
    conn.execute(
        text("INSERT INTO roles (id, name, description) VALUES (:id, :name, 'legacy')"),
        {"id": ids["role"], "name": f"legacy_member_{ids['role'].hex[:6]}"},
    )
    conn.execute(
        text("INSERT INTO permissions (id, name, resource, action) VALUES (:id, :name, 'project', 'read')"),
        {"id": ids["perm"], "name": f"project:read_{ids['perm'].hex[:6]}"},
    )
    conn.execute(
        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
        {"r": ids["role"], "p": ids["perm"]},
    )
    conn.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"),
        {"u": ids["user"], "r": ids["role"]},
    )
    conn.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, 'Drop Co', '1 rue', :u, NOW(), NOW())"
        ),
        {"id": ids["company"], "u": ids["user"]},
    )
    conn.execute(
        text("INSERT INTO projects (id, name, owner_id, company_id) VALUES (:id, 'Drop P', :u, :c)"),
        {"id": ids["project"], "u": ids["user"], "c": ids["company"]},
    )
    conn.execute(
        text(
            "INSERT INTO user_projects (user_id, project_id, role_id, assigned_at) "
            "VALUES (:u, :p, :r, NOW()) ON CONFLICT DO NOTHING"
        ),
        {"u": ids["user"], "p": ids["project"], "r": ids["role"]},
    )
    conn.execute(
        text(
            "INSERT INTO invitations "
            "(id, email, project_id, role_id, token_hash, status, expires_at, invited_by, created_at, updated_at) "
            "VALUES (:id, :email, :p, :r, :hash, 'pending', NOW() + INTERVAL '7 days', :u, NOW(), NOW())"
        ),
        {
            "id": ids["invitation"],
            "email": f"invitee-{ids['invitation'].hex[:8]}@migration-test.com",
            "p": ids["project"],
            "r": ids["role"],
            "hash": ids["invitation"].hex,
            "u": ids["user"],
        },
    )
    return ids


def _cleanup(conn, ids: dict) -> None:
    conn.execute(text("DELETE FROM invitations WHERE project_id = :p"), {"p": ids["project"]})
    conn.execute(text("DELETE FROM user_projects WHERE project_id = :p"), {"p": ids["project"]})
    conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": ids["project"]})
    conn.execute(text("DELETE FROM company_persons WHERE company_id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM persons WHERE user_id = :u"), {"u": ids["user"]})
    conn.execute(text("DELETE FROM user_company_access WHERE company_id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM companies WHERE id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["user"]})


def _reset_to_previous(app, cfg) -> None:
    """Land on _PREVIOUS from either side (fresh DB or already at head)."""
    from alembic import command

    _run(app, command.upgrade, cfg, _PREVIOUS)
    _run(app, command.downgrade, cfg, _PREVIOUS)


def test_migration_c2b8f1a0d743_round_trip(alembic_cfg, pg_engine, migration_app):
    from alembic import command

    _reset_to_previous(migration_app, alembic_cfg)

    with pg_engine.connect() as conn:
        assert _table_exists(conn, "roles")
        ids = _seed_legacy_state(conn)
        conn.commit()

    _run(migration_app, command.upgrade, alembic_cfg, _TARGET)

    with pg_engine.connect() as conn:
        for dropped in ("user_roles", "role_permissions", "roles", "permissions"):
            assert not _table_exists(conn, dropped), f"{dropped} still exists"
        assert not _column_exists(conn, "user_projects", "role_id")
        assert not _column_exists(conn, "invitations", "role_id")
        assert not _company_id_is_nullable(conn)

        # The assignment and the pending invitation are still there and usable.
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM user_projects WHERE user_id = :u AND project_id = :p"),
                {"u": ids["user"], "p": ids["project"]},
            ).scalar()
            == 1
        )
        assert (
            conn.execute(text("SELECT status FROM invitations WHERE id = :i"), {"i": ids["invitation"]}).scalar()
            == "pending"
        )

    _run(migration_app, command.downgrade, alembic_cfg, _PREVIOUS)

    with pg_engine.connect() as conn:
        for recreated in ("user_roles", "role_permissions", "roles", "permissions"):
            assert _table_exists(conn, recreated), f"{recreated} was not recreated"
            assert conn.execute(text(f"SELECT COUNT(*) FROM {recreated}")).scalar() == 0
        assert _column_exists(conn, "user_projects", "role_id")
        assert _column_exists(conn, "invitations", "role_id")
        assert _company_id_is_nullable(conn)

        _cleanup(conn, ids)
        conn.commit()

    _run(migration_app, command.upgrade, alembic_cfg, "head")


def test_upgrade_aborts_when_a_project_has_no_company(alembic_cfg, pg_engine, migration_app):
    """A project with no company would be unreachable — say which one, and stop."""
    from alembic import command

    _reset_to_previous(migration_app, alembic_cfg)

    orphan_id = uuid4()
    owner_id = uuid4()
    with pg_engine.connect() as conn:
        conn.execute(
            text("INSERT INTO users (id, email, password_hash, is_active) VALUES (:id, :email, 'x', TRUE)"),
            {"id": owner_id, "email": f"orphan-{owner_id.hex[:8]}@migration-test.com"},
        )
        conn.execute(
            text("INSERT INTO projects (id, name, owner_id, company_id) VALUES (:id, 'Orphan P', :u, NULL)"),
            {"id": orphan_id, "u": owner_id},
        )
        conn.commit()

    try:
        # `migrations/` is not an importable package (alembic loads revisions by
        # path), so the class is identified by name rather than imported.
        with pytest.raises(RuntimeError) as excinfo:
            _run(migration_app, command.upgrade, alembic_cfg, _TARGET)
        assert type(excinfo.value).__name__ == "OrphanProjectsError"
        assert str(orphan_id) in str(excinfo.value)

        with pg_engine.connect() as conn:
            # Nothing was dropped: the pre-flight ran before any DDL.
            assert _table_exists(conn, "roles")
            assert _company_id_is_nullable(conn)
    finally:
        with pg_engine.connect() as conn:
            conn.execute(text("DELETE FROM user_projects WHERE project_id = :p"), {"p": orphan_id})
            conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": orphan_id})
            conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": owner_id})
            conn.commit()
        _run(migration_app, command.upgrade, alembic_cfg, "head")
