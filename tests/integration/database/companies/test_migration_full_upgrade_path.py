"""Postgres-only: full Alembic upgrade/downgrade round-trip test (C3).

Tests that:
  1. Starting at down_revision (97e7156ea751 — billing module).
  2. Upgrade to 2d9c35848b9b (companies module) applies cleanly.
  3. After upgrade: companies, user_company_access, billing_documents tables exist.
  4. After upgrade: company_profile table does NOT exist.
  5. Downgrade -1 (back to 97e7156ea751) applies cleanly.
  6. After downgrade: companies and user_company_access tables are gone.

Skipped unless TEST_DATABASE_URL points at a real Postgres instance.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.requires_postgres

_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
if "sqlite" in _DB_URL:
    pytest.skip("Postgres-only: migration test requires real Postgres", allow_module_level=True)


@pytest.fixture(scope="module")
def alembic_cfg():
    """Build an Alembic Config pointing at the project's alembic.ini."""
    import pathlib
    from alembic.config import Config

    repo_root = pathlib.Path(__file__).parents[4]  # folio-back-end/
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _DB_URL)
    return cfg


@pytest.fixture(scope="module")
def pg_engine():
    """Create a raw SQLAlchemy engine for Postgres DDL inspection."""
    from sqlalchemy import create_engine

    engine = create_engine(_DB_URL, echo=False)
    yield engine
    engine.dispose()


def _table_exists(conn, table_name: str) -> bool:
    """Return True if table_name exists in the public schema."""
    from sqlalchemy import text

    result = conn.execute(
        text("SELECT COUNT(*) FROM information_schema.tables " "WHERE table_schema = 'public' AND table_name = :tbl"),
        {"tbl": table_name},
    )
    return result.scalar() == 1


def test_migration_full_upgrade_path(alembic_cfg, pg_engine):
    """C3: Alembic upgrade 97e7156ea751→2d9c35848b9b then downgrade -1 round-trips cleanly.

    Required by spec (C3):
      test_migration_full_upgrade_path
    """
    from alembic import command

    # Step 1 — Stamp DB at billing-module revision (down_revision for companies)
    command.stamp(alembic_cfg, "97e7156ea751")

    with pg_engine.connect() as conn:
        assert _table_exists(conn, "billing_documents"), "billing_documents must exist after stamping 97e7156ea751"
        assert not _table_exists(conn, "companies"), "companies table must NOT exist before upgrade"

    # Step 2 — Upgrade to companies module
    command.upgrade(alembic_cfg, "2d9c35848b9b")

    with pg_engine.connect() as conn:
        # Core companies tables created
        assert _table_exists(conn, "companies"), "companies table missing after upgrade"
        assert _table_exists(conn, "user_company_access"), "user_company_access table missing after upgrade"
        assert _table_exists(conn, "company_invite_tokens"), "company_invite_tokens table missing after upgrade"

        # C2 invariant: company_profile MUST NOT exist
        assert not _table_exists(
            conn, "company_profile"
        ), "company_profile table must NOT exist after upgrade — was it dropped?"

        # billing_documents still intact with company_id column
        assert _table_exists(conn, "billing_documents"), "billing_documents table must survive upgrade"
        result = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'billing_documents' AND column_name = 'company_id'"
            )
        )
        assert result.fetchone() is not None, "billing_documents.company_id column missing after upgrade"

    # Step 3 — Downgrade back one step (to 97e7156ea751)
    command.downgrade(alembic_cfg, "-1")

    with pg_engine.connect() as conn:
        # Companies tables removed
        assert not _table_exists(conn, "companies"), "companies table must be gone after downgrade"
        assert not _table_exists(conn, "user_company_access"), "user_company_access must be gone after downgrade"
        assert not _table_exists(conn, "company_invite_tokens"), "company_invite_tokens must be gone after downgrade"

        # billing_documents still intact
        assert _table_exists(conn, "billing_documents"), "billing_documents must survive downgrade"

    # Restore to head so subsequent tests have a clean DB
    command.upgrade(alembic_cfg, "head")
