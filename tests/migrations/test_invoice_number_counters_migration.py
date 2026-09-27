"""Postgres-only: invoice_number_counters migration round trip, and concurrent
expense creates that all get distinct numbers from the counter.

Skipped unless TEST_DATABASE_URL points at a real Postgres instance.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.requires_postgres

_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
if "sqlite" in _DB_URL:
    pytest.skip("Postgres-only: migration test requires real Postgres", allow_module_level=True)

_BEFORE = "b6d2f4a8c1e3"
_THIS = "c3e7a91d5f20"


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


def _table_exists(conn, name: str) -> bool:
    return (
        conn.execute(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = :t"),
            {"t": name},
        ).scalar()
        == 1
    )


def _seed_project(conn):
    user_id, project_id, now = uuid4(), uuid4(), datetime.now(timezone.utc)
    conn.execute(
        text("INSERT INTO users (id, email, is_active, created_at, updated_at) VALUES (:id, :e, true, :n, :n)"),
        {"id": user_id, "e": f"{user_id}@test.local", "n": now},
    )
    company_id = uuid4()
    conn.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, default_phone_region, created_by, created_at, updated_at) "
            "VALUES (:id, 'C', 'A', 'FR', :o, :n, :n)"
        ),
        {"id": company_id, "o": user_id, "n": now},
    )
    conn.execute(
        text(
            "INSERT INTO projects (id, name, owner_id, company_id, created_at, updated_at) "
            "VALUES (:id, 'P', :o, :c, :n, :n)"
        ),
        {"id": project_id, "o": user_id, "c": company_id, "n": now},
    )
    return user_id, project_id


def _insert_invoice(conn, project_id, user_id, number):
    now = datetime.now(timezone.utc)
    conn.execute(
        text(
            "INSERT INTO invoices (id, project_id, invoice_number, type, issue_date, recipient_name, items, "
            " created_by, created_at, updated_at) "
            "VALUES (:id, :p, :num, 'others', :d, 'S', '[]'::jsonb, :u, :n, :n)"
        ),
        {"id": uuid4(), "p": project_id, "num": number, "d": date.today(), "u": user_id, "n": now},
    )


def test_migration_seeds_counters_and_round_trips(alembic_cfg, pg_engine, migration_app):
    from alembic import command

    # From an empty or an up-to-date database alike, stand on the previous revision.
    _run(migration_app, command.upgrade, alembic_cfg, _BEFORE)
    _run(migration_app, command.downgrade, alembic_cfg, _BEFORE)
    with pg_engine.begin() as conn:
        assert not _table_exists(conn, "invoice_number_counters")
        user_id, project_id = _seed_project(conn)
        for number in ("INV-2026-0009", "INV-2026-10000", "FR-2026-0003", "ABC-2025-0002"):
            _insert_invoice(conn, project_id, user_id, number)

    _run(migration_app, command.upgrade, alembic_cfg, _THIS)
    with pg_engine.connect() as conn:
        rows = dict(
            conn.execute(
                text("SELECT prefix, next_value FROM invoice_number_counters WHERE project_id = :p"),
                {"p": project_id},
            ).fetchall()
        )
    assert rows == {"INV-2026-": 10001, "FR-2026-": 4, "ABC-2025-": 3}

    _run(migration_app, command.downgrade, alembic_cfg, _BEFORE)
    with pg_engine.connect() as conn:
        assert not _table_exists(conn, "invoice_number_counters")

    _run(migration_app, command.upgrade, alembic_cfg, "head")


def test_concurrent_creates_all_get_distinct_numbers(pg_engine, migration_app):
    from sqlalchemy.orm import Session

    from app.domain.entities.invoice import Invoice, InvoiceType
    from app.infrastructure.adapters.sqlalchemy_invoice import SQLAlchemyInvoiceRepository

    with pg_engine.begin() as conn:
        if not _table_exists(conn, "invoice_number_counters"):
            pytest.skip("schema not migrated to head")
        user_id, project_id = _seed_project(conn)

    barrier = threading.Barrier(6)
    numbers, errors = [], []

    def create():
        with Session(pg_engine) as session:
            repo = SQLAlchemyInvoiceRepository(session)
            barrier.wait()
            try:
                now = datetime.now(timezone.utc)
                inv = repo.create(
                    Invoice(
                        id=uuid4(),
                        project_id=project_id,
                        invoice_number=repo.next_invoice_number(project_id),
                        type=InvoiceType.OTHERS,
                        issue_date=date.today(),
                        recipient_name="S",
                        created_by=user_id,
                        created_at=now,
                        updated_at=now,
                        items=[],
                    )
                )
                numbers.append(inv.invoice_number)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    threads = [threading.Thread(target=create) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(set(numbers)) == 6
