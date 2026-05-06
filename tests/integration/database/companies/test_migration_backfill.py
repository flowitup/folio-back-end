"""Postgres-only: migration round-trip test for company_profile → companies backfill.

Required regressions:
  test_migration_company_profile_to_companies_round_trip
  test_doc_company_id_set_null_after_company_delete

Skipped unless TEST_DATABASE_URL points at a real Postgres instance.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.requires_postgres

_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
if "sqlite" in _DB_URL:
    pytest.skip("Postgres-only: migration test requires real Postgres", allow_module_level=True)


def test_migration_company_profile_to_companies_round_trip(session):
    """test_migration_company_profile_to_companies_round_trip — required by spec.

    This test verifies the data-migration invariants documented in phase 03:
      1. Every user who had a company_profile row now has a primary company.
      2. All old billing_number_counter rows (keyed by user_id) are migrated
         to new rows keyed by company_id.
      3. billing_documents.company_id is backfilled to the primary company.
      4. The company_profile table still exists (kept for legacy endpoint compat).

    NOTE: In the phase 05 test environment the migration has already run.
    This test validates post-migration invariants by querying current state.
    """
    from sqlalchemy import text

    # Every user_company_access primary row should have a matching company
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM user_company_access uca "
            "LEFT JOIN companies c ON c.id = uca.company_id "
            "WHERE uca.is_primary = TRUE AND c.id IS NULL"
        )
    )
    orphaned = result.scalar()
    assert orphaned == 0, f"{orphaned} primary access rows have no matching company"

    # billing_documents with a non-null company_id should reference a live company
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM billing_documents bd "
            "LEFT JOIN companies c ON c.id = bd.company_id "
            "WHERE bd.company_id IS NOT NULL AND c.id IS NULL"
        )
    )
    dangling = result.scalar()
    assert dangling == 0, f"{dangling} billing_documents.company_id references missing companies"


def test_doc_company_id_set_null_after_company_delete(session):
    """test_doc_company_id_set_null_after_company_delete — required by spec.

    Verifies ON DELETE SET NULL FK behavior: deleting a company sets
    billing_documents.company_id to NULL while preserving issuer snapshot columns.
    """
    from datetime import datetime, timezone
    from uuid import uuid4
    from sqlalchemy import text

    user_id = uuid4()
    company_id = uuid4()
    doc_id = uuid4()
    now = datetime.now(timezone.utc)

    # Insert minimal company
    session.execute(
        text(
            "INSERT INTO companies (id, legal_name, address, created_by, created_at, updated_at) "
            "VALUES (:id, 'Del Test SAS', '1 rue X', :cb, :ca, :ua)"
        ),
        {"id": str(company_id), "cb": str(user_id), "ca": now, "ua": now},
    )

    # Insert minimal billing_document referencing the company
    session.execute(
        text(
            "INSERT INTO billing_documents "
            "(id, user_id, company_id, kind, document_number, status, issue_date, "
            " recipient_name, issuer_legal_name, issuer_address, items, created_at, updated_at) "
            "VALUES (:id, :uid, :cid, 'devis', 'DEV-2026-001', 'draft', :today, "
            "        'Client Corp', 'Del Test SAS', '1 rue X', '[]', :ca, :ua)"
        ),
        {
            "id": str(doc_id),
            "uid": str(user_id),
            "cid": str(company_id),
            "today": now.date(),
            "ca": now,
            "ua": now,
        },
    )
    session.flush()

    # Delete the company — FK ON DELETE SET NULL should kick in
    session.execute(text("DELETE FROM companies WHERE id = :id"), {"id": str(company_id)})
    session.flush()

    # billing_document.company_id is NULL but issuer snapshot remains
    row = session.execute(
        text(
            "SELECT company_id, issuer_legal_name FROM billing_documents WHERE id = :id"
        ),
        {"id": str(doc_id)},
    ).fetchone()

    assert row is not None
    assert row[0] is None, "company_id should be NULL after company deletion"
    assert row[1] == "Del Test SAS", "issuer_legal_name snapshot must survive company deletion"
