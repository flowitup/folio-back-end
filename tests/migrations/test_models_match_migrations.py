"""Postgres-only: the ORM models describe exactly the schema the migrations build.

A drift here means the next ``flask db migrate`` would propose dropping (or
altering) what the migrations created, e.g. the partial unique indexes that
carry business rules, and that the SQLite test schema built with
``create_all()`` lacks them.

Skipped unless TEST_DATABASE_URL (or DATABASE_URL) points at a real Postgres —
same pattern as tests/migrations/test_ops_flag_migration.py. The database is
upgraded to head.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.requires_postgres

_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL", "sqlite:///:memory:")
if "postgresql" not in _DB_URL:
    pytest.skip("Postgres-only: migration test requires real Postgres", allow_module_level=True)


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


def test_models_match_the_migrated_schema(alembic_cfg, migration_app):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from app.infrastructure.database.models import Base

    with migration_app.app_context():
        command.upgrade(alembic_cfg, "head")

    engine = create_engine(_DB_URL, echo=False)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []
