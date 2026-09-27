"""Alembic round-trip for b4d8e2f6a1c3 (login_otps.purpose).

Runs the migration's own upgrade()/downgrade() against a throwaway SQLite database holding the
pre-migration ``login_otps`` table: existing rows are backfilled as ``sign_in``, and the
downgrade removes phone-change codes (which the older code would treat as sign-in codes) before
dropping the column.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = Path(__file__).parents[2] / "migrations" / "versions" / "b4d8e2f6a1c3_login_otps_purpose.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("login_otps_purpose", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(conn, fn) -> None:
    import alembic.op as op_module

    ops = Operations(MigrationContext.configure(conn))
    op_module._proxy = ops  # what alembic's runner installs behind `from alembic import op`
    try:
        fn()
    finally:
        del op_module._proxy


def _columns(conn) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns("login_otps")}


def test_upgrade_backfills_sign_in_and_downgrade_drops_phone_change_codes():
    migration = _load_migration()
    assert migration.down_revision == "c3e7a91d5f20"
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE login_otps (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36), phone VARCHAR(20) NOT NULL,"
                " code_hash VARCHAR(64) NOT NULL, expires_at TIMESTAMP NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,"
                " consumed_at TIMESTAMP, created_at TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO login_otps (id, phone, code_hash, expires_at, created_at)"
                " VALUES ('old', '+33600000001', 'h', '2026-09-27', '2026-09-27')"
            )
        )

        _run(conn, migration.upgrade)
        assert "purpose" in _columns(conn)
        assert conn.execute(sa.text("SELECT purpose FROM login_otps WHERE id = 'old'")).scalar_one() == "sign_in"
        conn.execute(
            sa.text(
                "INSERT INTO login_otps (id, phone, code_hash, expires_at, created_at, purpose)"
                " VALUES ('change', '+33600000002', 'h', '2026-09-27', '2026-09-27', 'phone_change')"
            )
        )

        _run(conn, migration.downgrade)
        assert "purpose" not in _columns(conn)
        assert [r[0] for r in conn.execute(sa.text("SELECT id FROM login_otps"))] == ["old"]
