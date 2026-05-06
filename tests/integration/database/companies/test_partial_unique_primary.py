"""Postgres-only: partial UNIQUE WHERE is_primary=TRUE index test.

Required regression: test_partial_unique_blocks_dual_primary

Skipped unless TEST_DATABASE_URL points at a real Postgres instance.
The SQLite in-memory engine does not support partial unique indexes.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.requires_postgres

# Skip entire module when running against SQLite
_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
if "sqlite" in _DB_URL:
    pytest.skip("Postgres-only: partial unique index not supported on SQLite", allow_module_level=True)


@pytest.fixture
def pg_session(engine, tables):
    """Session using the real Postgres engine from conftest."""
    from sqlalchemy.orm import sessionmaker
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


def test_partial_unique_blocks_dual_primary(pg_session):
    """test_partial_unique_blocks_dual_primary — required by spec.

    Direct INSERT of two is_primary=TRUE rows for the same user_id must raise
    IntegrityError due to the partial unique index:
      CREATE UNIQUE INDEX uq_user_company_access_one_primary
      ON user_company_access (user_id) WHERE is_primary = TRUE;
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    user_id = uuid4()
    now = datetime.now(timezone.utc)

    # First company + access row (primary=TRUE)
    cid1, cid2 = uuid4(), uuid4()
    pg_session.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, :name, :addr, :cb, :ca, :ua)"
        ),
        {"id": str(cid1), "name": "Corp A", "addr": "1 rue A", "cb": str(user_id), "ca": now, "ua": now},
    )
    pg_session.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, :name, :addr, :cb, :ca, :ua)"
        ),
        {"id": str(cid2), "name": "Corp B", "addr": "2 rue B", "cb": str(user_id), "ca": now, "ua": now},
    )
    pg_session.execute(
        text(
            "INSERT INTO user_company_access (user_id, company_id, is_primary, attached_at) "
            "VALUES (:uid, :cid, TRUE, :at)"
        ),
        {"uid": str(user_id), "cid": str(cid1), "at": now},
    )
    pg_session.flush()

    with pytest.raises(IntegrityError):
        pg_session.execute(
            text(
                "INSERT INTO user_company_access (user_id, company_id, is_primary, attached_at) "
                "VALUES (:uid, :cid, TRUE, :at)"
            ),
            {"uid": str(user_id), "cid": str(cid2), "at": now},
        )
        pg_session.flush()
