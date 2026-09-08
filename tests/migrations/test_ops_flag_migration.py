"""Postgres-only: Alembic round-trip for migration 9a4c1e7b2d05 (platform-ops flag).

Covers:
  - upgrade from 7d3e9a1b4c5f adds users.is_platform_ops NOT NULL DEFAULT false;
  - the data step maps a legacy `*:*` holder to ops + admin of their company,
    assigns the project creator and gives every attachment a directory profile;
  - downgrade drops the column (the rows the data step created stay: they are
    indistinguishable from ones an admin created afterwards).

Skipped unless TEST_DATABASE_URL (or DATABASE_URL) points at a real Postgres —
same pattern as tests/migrations/test_company_persons_migration.py.
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

_PREVIOUS = "7d3e9a1b4c5f"
_TARGET = "9a4c1e7b2d05"


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


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM information_schema.columns WHERE table_name = :tbl AND column_name = :col"),
            {"tbl": table_name, "col": column_name},
        ).scalar()
        == 1
    )


def _seed_legacy_state(conn) -> dict:
    """A legacy `*:*` holder attached as member, owning a project of that company."""
    ids = {k: uuid4() for k in ("user", "role", "perm", "company", "project")}
    conn.execute(
        text("INSERT INTO users (id, email, password_hash, is_active) VALUES (:id, :email, 'x', TRUE)"),
        {"id": ids["user"], "email": f"ops-{ids['user'].hex[:8]}@migration-test.com"},
    )
    conn.execute(
        text("INSERT INTO roles (id, name, description) VALUES (:id, :name, 'legacy')"),
        {"id": ids["role"], "name": f"legacy_admin_{ids['role'].hex[:6]}"},
    )
    # The creator-assignment step needs SOME legacy role to satisfy the NOT NULL
    # `user_projects.role_id` of this revision. The chain seeds one, but a later
    # revision's downgrade recreates `roles` empty, so seed it here too.
    conn.execute(
        text(
            "INSERT INTO roles (id, name, description) VALUES (:id, 'manager', 'legacy') "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"id": uuid4()},
    )
    conn.execute(
        text("INSERT INTO permissions (id, name, resource, action) VALUES (:id, '*:*', '*', '*') "),
        {"id": ids["perm"]},
    )
    conn.execute(
        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:r, :p)"),
        {"r": ids["role"], "p": ids["perm"]},
    )
    conn.execute(
        text("INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": ids["user"], "r": ids["role"]}
    )
    conn.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, 'Migration Co', '1 rue', :u, NOW(), NOW())"
        ),
        {"id": ids["company"], "u": ids["user"]},
    )
    conn.execute(
        text(
            "INSERT INTO user_company_access (user_id, company_id, is_primary, role, attached_at) "
            "VALUES (:u, :c, TRUE, 'member', NOW())"
        ),
        {"u": ids["user"], "c": ids["company"]},
    )
    conn.execute(
        text("INSERT INTO projects (id, name, owner_id, company_id) VALUES (:id, 'Migration P', :u, :c)"),
        {"id": ids["project"], "u": ids["user"], "c": ids["company"]},
    )
    return ids


def _cleanup(conn, ids: dict) -> None:
    conn.execute(text("DELETE FROM user_projects WHERE project_id = :p"), {"p": ids["project"]})
    conn.execute(text("DELETE FROM projects WHERE id = :p"), {"p": ids["project"]})
    conn.execute(
        text("DELETE FROM company_persons WHERE company_id = :c"),
        {"c": ids["company"]},
    )
    conn.execute(text("DELETE FROM persons WHERE user_id = :u"), {"u": ids["user"]})
    conn.execute(text("DELETE FROM user_company_access WHERE company_id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM companies WHERE id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM user_roles WHERE user_id = :u"), {"u": ids["user"]})
    conn.execute(text("DELETE FROM role_permissions WHERE role_id = :r"), {"r": ids["role"]})
    conn.execute(text("DELETE FROM roles WHERE id = :r"), {"r": ids["role"]})
    conn.execute(text("DELETE FROM permissions WHERE id = :p"), {"p": ids["perm"]})
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["user"]})


def test_migration_9a4c1e7b2d05_round_trip(alembic_cfg, pg_engine, migration_app):
    from alembic import command

    # Reach the pre-migration revision from either side: the database may be
    # fresh (below it) or already at head (another migration test ran first).
    _run(migration_app, command.upgrade, alembic_cfg, _PREVIOUS)
    _run(migration_app, command.downgrade, alembic_cfg, _PREVIOUS)

    with pg_engine.connect() as conn:
        assert not _column_exists(conn, "users", "is_platform_ops")
        ids = _seed_legacy_state(conn)
        conn.commit()

    _run(migration_app, command.upgrade, alembic_cfg, _TARGET)

    with pg_engine.connect() as conn:
        assert _column_exists(conn, "users", "is_platform_ops")

        assert (
            conn.execute(text("SELECT is_platform_ops FROM users WHERE id = :u"), {"u": ids["user"]}).scalar() is True
        )
        assert (
            conn.execute(
                text("SELECT role FROM user_company_access WHERE user_id = :u AND company_id = :c"),
                {"u": ids["user"], "c": ids["company"]},
            ).scalar()
            == "admin"
        )
        # Creator assignment (the owner bypass is gone).
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM user_projects WHERE user_id = :u AND project_id = :p"),
                {"u": ids["user"], "p": ids["project"]},
            ).scalar()
            == 1
        )
        # Directory profile so the assign-member pickers can see them.
        assert (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM company_persons cp JOIN persons p ON p.id = cp.person_id "
                    "WHERE cp.company_id = :c AND p.user_id = :u AND cp.is_active"
                ),
                {"c": ids["company"], "u": ids["user"]},
            ).scalar()
            == 1
        )

    _run(migration_app, command.downgrade, alembic_cfg, _PREVIOUS)

    with pg_engine.connect() as conn:
        assert not _column_exists(conn, "users", "is_platform_ops")
        _cleanup(conn, ids)
        conn.commit()

    _run(migration_app, command.upgrade, alembic_cfg, "head")


def _seed_phone_collision(conn) -> dict:
    """A user whose phone is already taken in their company by a pending profile.

    Postgres keeps `UNIQUE(company_id, phone_normalized) WHERE phone_normalized
    IS NOT NULL` on `company_persons` (migration 2ca24be9e3a8) — the exact index
    a naive directory backfill violates, aborting the deploy half-way.
    """
    ids = {k: uuid4() for k in ("user", "company", "person", "pending_person", "pending_profile")}
    phone = f"+3360000{ids['user'].int % 10000:04d}"
    ids["phone"] = phone
    conn.execute(
        text("INSERT INTO users (id, email, password_hash, is_active) VALUES (:id, :email, 'x', TRUE)"),
        {"id": ids["user"], "email": f"phone-{ids['user'].hex[:8]}@migration-test.com"},
    )
    conn.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, 'Phone Collision Co', '1 rue', :u, NOW(), NOW())"
        ),
        {"id": ids["company"], "u": ids["user"]},
    )
    conn.execute(
        text(
            "INSERT INTO user_company_access (user_id, company_id, is_primary, role, attached_at) "
            "VALUES (:u, :c, TRUE, 'member', NOW())"
        ),
        {"u": ids["user"], "c": ids["company"]},
    )
    # The user's own identity carries the phone…
    conn.execute(
        text(
            "INSERT INTO persons (id, name, normalized_name, phone, phone_normalized, user_id, "
            "created_by_user_id, created_at, updated_at) "
            "VALUES (:id, 'Attached User', 'attached user', :phone, :phone, :u, :u, NOW(), NOW())"
        ),
        {"id": ids["person"], "phone": phone, "u": ids["user"]},
    )
    # …and an admin already added a pending profile for the same number.
    conn.execute(
        text(
            "INSERT INTO persons (id, name, normalized_name, phone, phone_normalized, "
            "created_by_user_id, created_at, updated_at) "
            "VALUES (:id, 'Pending Worker', 'pending worker', :phone, :phone, :u, NOW(), NOW())"
        ),
        {"id": ids["pending_person"], "phone": phone, "u": ids["user"]},
    )
    conn.execute(
        text(
            "INSERT INTO company_persons (id, company_id, person_id, is_active, phone_normalized, "
            "created_by_user_id, created_at) "
            "VALUES (:id, :c, :p, TRUE, :phone, :u, NOW())"
        ),
        {
            "id": ids["pending_profile"],
            "c": ids["company"],
            "p": ids["pending_person"],
            "phone": phone,
            "u": ids["user"],
        },
    )
    return ids


def _cleanup_phone_collision(conn, ids: dict) -> None:
    conn.execute(text("DELETE FROM company_persons WHERE company_id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM user_company_access WHERE company_id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM companies WHERE id = :c"), {"c": ids["company"]})
    conn.execute(text("DELETE FROM persons WHERE id IN (:p1, :p2)"), {"p1": ids["person"], "p2": ids["pending_person"]})
    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["user"]})


def test_directory_backfill_survives_a_phone_already_used_in_the_company(alembic_cfg, pg_engine, migration_app):
    from alembic import command

    _run(migration_app, command.upgrade, alembic_cfg, _PREVIOUS)
    _run(migration_app, command.downgrade, alembic_cfg, _PREVIOUS)

    with pg_engine.connect() as conn:
        ids = _seed_phone_collision(conn)
        conn.commit()

    # Must not raise: an IntegrityError here aborts the deploy half-way.
    _run(migration_app, command.upgrade, alembic_cfg, _TARGET)

    with pg_engine.connect() as conn:
        # The attached user is listed, without the phone the pending row owns.
        linked_phone = conn.execute(
            text(
                "SELECT cp.phone_normalized FROM company_persons cp " "WHERE cp.company_id = :c AND cp.person_id = :p"
            ),
            {"c": ids["company"], "p": ids["person"]},
        ).fetchone()
        assert linked_phone is not None, "the attachment must still produce a directory profile"
        assert linked_phone[0] is None
        # The phone stays unique inside the company (the partial index holds).
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM company_persons " "WHERE company_id = :c AND phone_normalized = :phone"),
                {"c": ids["company"], "phone": ids["phone"]},
            ).scalar()
            == 1
        )

    _run(migration_app, command.downgrade, alembic_cfg, _PREVIOUS)

    with pg_engine.connect() as conn:
        _cleanup_phone_collision(conn, ids)
        conn.commit()

    _run(migration_app, command.upgrade, alembic_cfg, "head")
