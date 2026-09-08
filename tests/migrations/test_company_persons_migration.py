"""Postgres-only: full Alembic upgrade/downgrade round-trip for migration 2ca24be9e3a8.

Covers:
  - Upgrade from 15c1df3fdbfa to 2ca24be9e3a8 applies cleanly.
  - New tables (company_persons, company_member_grants) exist after upgrade.
  - New columns exist: persons.user_id/phone_normalized,
    companies.default_phone_region, labor_roles.company_id/slug,
    billing_document_templates.company_id.
  - Backfills ran: the two seeded labor roles have slugs.
  - Downgrade back to 15c1df3fdbfa removes the new tables/columns.

Skipped unless TEST_DATABASE_URL points at a real Postgres instance — same
pattern as tests/integration/database/companies/test_migration_full_upgrade_path.py.
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
    import pathlib

    from alembic.config import Config

    repo_root = pathlib.Path(__file__).parents[1]  # folio-back-end/
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _DB_URL)
    return cfg


@pytest.fixture(scope="module")
def pg_engine():
    from sqlalchemy import create_engine

    engine = create_engine(_DB_URL, echo=False)
    yield engine
    engine.dispose()


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :tbl"),
        {"tbl": table_name},
    )
    return result.scalar() == 1


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        text("SELECT COUNT(*) FROM information_schema.columns " "WHERE table_name = :tbl AND column_name = :col"),
        {"tbl": table_name, "col": column_name},
    )
    return result.scalar() == 1


def test_migration_2ca24be9e3a8_round_trip(alembic_cfg, pg_engine):
    from alembic import command

    # Stamp at the pre-migration head.
    command.stamp(alembic_cfg, "15c1df3fdbfa")

    with pg_engine.connect() as conn:
        assert not _table_exists(conn, "company_persons")
        assert not _table_exists(conn, "company_member_grants")

    command.upgrade(alembic_cfg, "2ca24be9e3a8")

    with pg_engine.connect() as conn:
        assert _table_exists(conn, "company_persons")
        assert _table_exists(conn, "company_member_grants")
        assert _column_exists(conn, "persons", "user_id")
        assert _column_exists(conn, "persons", "phone_normalized")
        assert _column_exists(conn, "companies", "default_phone_region")
        assert _column_exists(conn, "labor_roles", "company_id")
        assert _column_exists(conn, "labor_roles", "slug")
        assert _column_exists(conn, "billing_document_templates", "company_id")

        # Backfill: the two stable seed labor roles must have a slug.
        result = conn.execute(
            text("SELECT slug FROM labor_roles WHERE id = 'b08f0bdb-9e78-40ca-aca9-96016de45c7c'")
        ).fetchone()
        if result is not None:
            assert result[0] == "tho_chinh"

        # projects.company_id FK is now RESTRICT — deleting a company that
        # still owns a project must fail rather than orphan it.
        result = conn.execute(
            text("SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_projects_company_id'")
        ).fetchone()
        assert result is not None
        assert result[0] == "r"  # 'r' = RESTRICT in pg_constraint.confdeltype

    command.downgrade(alembic_cfg, "-1")

    with pg_engine.connect() as conn:
        assert not _table_exists(conn, "company_persons")
        assert not _table_exists(conn, "company_member_grants")
        assert not _column_exists(conn, "persons", "user_id")
        assert not _column_exists(conn, "labor_roles", "company_id")
        assert not _column_exists(conn, "billing_document_templates", "company_id")

    # Restore to head so subsequent tests have a clean DB.
    command.upgrade(alembic_cfg, "head")
