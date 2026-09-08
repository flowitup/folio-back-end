"""Backfill `company_persons` for persons that predate the company directory (Phase 2 follow-up).

Migration `2ca24be9e3a8` created `company_persons` but did not give existing
persons a profile, so on a deployed database the company directory and the
company-scoped persons search start empty even though the same people already
work on the company's projects. Two passes, in Python so the same code runs
on Postgres (deploy) and SQLite (tests):

  1. every distinct (company, person) pair reachable through
     `workers.person_id → projects.company_id` gets an active profile;
     the labor role and daily rate come from that person's most recent
     worker row in that company.
  2. persons still without any profile are attached to the single company
     in the database — only when exactly one company exists; otherwise they
     stay unlisted for a human to place, and the migration prints how many.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

_PAIRS_FROM_WORKERS_SQL = text(
    """
    SELECT DISTINCT p.company_id AS company_id, w.person_id AS person_id
    FROM workers w
    JOIN projects p ON p.id = w.project_id
    WHERE w.person_id IS NOT NULL AND p.company_id IS NOT NULL
    """
)

_EXISTING_PROFILE_SQL = text(
    """
    SELECT COUNT(*) FROM company_persons
    WHERE CAST(company_id AS TEXT) = CAST(:company_id AS TEXT)
      AND CAST(person_id AS TEXT) = CAST(:person_id AS TEXT)
    """
)

_LATEST_WORKER_SQL = text(
    """
    SELECT w.role_id, w.daily_rate
    FROM workers w JOIN projects p ON p.id = w.project_id
    WHERE CAST(w.person_id AS TEXT) = CAST(:person_id AS TEXT)
      AND CAST(p.company_id AS TEXT) = CAST(:company_id AS TEXT)
    ORDER BY w.created_at DESC
    LIMIT 1
    """
)

_PERSONS_WITHOUT_PROFILE_SQL = text(
    """
    SELECT id, phone_normalized FROM persons pe
    WHERE NOT EXISTS (SELECT 1 FROM company_persons cp WHERE cp.person_id = pe.id)
    """
)

_SOLE_COMPANY_SQL = text("SELECT id FROM companies")

_INSERT_SQL = text(
    """
    INSERT INTO company_persons
      (id, company_id, person_id, labor_role_id, default_daily_rate, is_active, phone_normalized, created_at)
    VALUES (:id, :company_id, :person_id, :labor_role_id, :default_daily_rate, :is_active, :phone_normalized, :created_at)
    """
)


def _insert_profile(conn: Connection, company_id, person_id, phone_normalized=None) -> None:
    worker = conn.execute(_LATEST_WORKER_SQL, {"person_id": str(person_id), "company_id": str(company_id)}).fetchone()
    conn.execute(
        _INSERT_SQL,
        {
            # SQLite stores UUID(as_uuid=True) columns as 32-char hex and cannot bind UUID objects.
            "id": uuid4().hex if conn.dialect.name == "sqlite" else uuid4(),
            "company_id": company_id,
            "person_id": person_id,
            "labor_role_id": worker[0] if worker else None,
            "default_daily_rate": worker[1] if worker else None,
            "is_active": True,
            "phone_normalized": phone_normalized,
            "created_at": datetime.now(timezone.utc),
        },
    )


def backfill_from_workers(conn: Connection) -> int:
    """Pass 1: a profile per (company, person) pair implied by the person's worker rows."""
    inserted = 0
    for company_id, person_id in conn.execute(_PAIRS_FROM_WORKERS_SQL).fetchall():
        exists = conn.execute(
            _EXISTING_PROFILE_SQL, {"company_id": str(company_id), "person_id": str(person_id)}
        ).scalar()
        if exists:
            continue
        _insert_profile(conn, company_id, person_id)
        inserted += 1
    return inserted


def backfill_from_sole_company(conn: Connection) -> int:
    """Pass 2: persons with no profile join the single company, when exactly one exists."""
    companies = conn.execute(_SOLE_COMPANY_SQL).fetchall()
    if len(companies) != 1:
        return 0
    company_id = companies[0][0]
    inserted = 0
    for person_id, phone_normalized in conn.execute(_PERSONS_WITHOUT_PROFILE_SQL).fetchall():
        _insert_profile(conn, company_id, person_id, phone_normalized)
        inserted += 1
    return inserted


def count_persons_without_profile(conn: Connection) -> int:
    return len(conn.execute(_PERSONS_WITHOUT_PROFILE_SQL).fetchall())


def run_backfill(conn: Connection) -> tuple[int, int, int]:
    """Run both passes; return (from_workers, from_sole_company, still_without_profile)."""
    a = backfill_from_workers(conn)
    b = backfill_from_sole_company(conn)
    return a, b, count_persons_without_profile(conn)
