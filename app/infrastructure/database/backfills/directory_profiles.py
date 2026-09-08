"""Every company attachment gets a directory profile (step 4).

Invariant: every ``user_company_access`` row has an active ``company_persons``
profile linked to the user. Users attached before the company directory shipped
have no profile, so the assign-member pickers cannot see them. Missing
``persons`` rows are derived from the user's display name (or email) and phone.

Phone collisions: PostgreSQL enforces a partial
``UNIQUE(company_id, phone_normalized) WHERE phone_normalized IS NOT NULL`` on
``company_persons`` (see migration ``2ca24be9e3a8``; SQLite has no such index,
so the test suite cannot reproduce it). When an admin already added someone by
that phone — a pending profile for a different `persons` row — the profile is
created without the phone rather than violating the index and aborting the
whole migration. The two rows stay separate identities; linking them is the
add-by-phone/claim flow's job, with a human deciding.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.infrastructure.database.backfills.authz_backfill_report import (
    BackfillReport,
    company_label,
    user_identity,
    user_label,
)

_ACCESS_PAIRS_SQL = text("SELECT user_id, company_id FROM user_company_access")

_PERSON_BY_USER_SQL = text(
    "SELECT id, phone_normalized FROM persons WHERE CAST(user_id AS TEXT) = CAST(:user_id AS TEXT) LIMIT 1"
)

_INSERT_PERSON_SQL = text(
    """
    INSERT INTO persons
      (id, name, normalized_name, phone, phone_normalized, user_id, created_by_user_id, created_at, updated_at)
    VALUES (:id, :name, :normalized_name, :phone, :phone_normalized, :user_id, :user_id, :created_at, :created_at)
    """
)

_COMPANY_PERSON_SQL = text(
    """
    SELECT id, is_active FROM company_persons
    WHERE CAST(company_id AS TEXT) = CAST(:company_id AS TEXT)
      AND CAST(person_id AS TEXT) = CAST(:person_id AS TEXT)
    LIMIT 1
    """
)

_PHONE_TAKEN_SQL = text(
    """
    SELECT 1 FROM company_persons
    WHERE CAST(company_id AS TEXT) = CAST(:company_id AS TEXT)
      AND phone_normalized = :phone_normalized
    LIMIT 1
    """
)

_REACTIVATE_COMPANY_PERSON_SQL = text(
    """
    UPDATE company_persons SET is_active = TRUE, pending_expires_at = NULL
    WHERE CAST(id AS TEXT) = CAST(:id AS TEXT)
    """
)

_INSERT_COMPANY_PERSON_SQL = text(
    """
    INSERT INTO company_persons
      (id, company_id, person_id, labor_role_id, default_daily_rate, is_active,
       phone_normalized, created_by_user_id, created_at)
    VALUES (:id, :company_id, :person_id, NULL, NULL, TRUE, :phone_normalized, :user_id, :created_at)
    """
)


def _person_for_user(conn: Connection, user_id, now: datetime, report: BackfillReport):
    """Return `(person_id, phone_normalized)` for a user, creating the identity if needed."""
    row = conn.execute(_PERSON_BY_USER_SQL, {"user_id": str(user_id)}).fetchone()
    if row is not None:
        # Keep the phone the existing identity already carries: dropping it here
        # is what would make a later add-by-phone fail to dedup.
        return row[0], row[1]

    identity = user_identity(conn, user_id)
    if identity is None:
        return None, None
    display_name, email, phone = identity[0] or identity[1], identity[1], identity[2]
    person_id = uuid4().hex if conn.dialect.name == "sqlite" else uuid4()
    conn.execute(
        _INSERT_PERSON_SQL,
        {
            "id": person_id,
            "name": display_name or email,
            "normalized_name": (display_name or "").strip().lower(),
            "phone": phone,
            "phone_normalized": phone,
            "user_id": user_id,
            "created_at": now,
        },
    )
    report.persons_created += 1
    return person_id, phone


def _phone_for_profile(conn: Connection, company_id, phone_normalized, user_id, report: BackfillReport):
    """The phone to store on the new profile — None when this company already uses it."""
    if phone_normalized is None:
        return None
    taken = conn.execute(
        _PHONE_TAKEN_SQL, {"company_id": str(company_id), "phone_normalized": phone_normalized}
    ).fetchone()
    if taken is None:
        return phone_normalized
    report.profiles_without_phone += 1
    report.warnings.append(
        f"  directory: {user_label(conn, user_id)} listed in {company_label(conn, company_id)} without their "
        "phone — another profile of that company already uses it (merge them from the directory)"
    )
    return None


def ensure_directory_profile(
    conn: Connection,
    user_id,
    company_id,
    report: BackfillReport,
    now: "datetime | None" = None,
) -> None:
    """Give one company attachment an active, user-linked directory profile.

    Idempotent: an existing profile is reactivated if it was archived and left
    alone otherwise. Shared with `scripts/qa_personas.py`, which attaches its
    personas one at a time.
    """
    now = now or datetime.now(timezone.utc)
    person_id, phone_normalized = _person_for_user(conn, user_id, now, report)
    if person_id is None:
        return

    profile = conn.execute(_COMPANY_PERSON_SQL, {"company_id": str(company_id), "person_id": str(person_id)}).fetchone()
    if profile is not None:
        if not profile[1]:
            conn.execute(_REACTIVATE_COMPANY_PERSON_SQL, {"id": profile[0]})
            report.profiles_reactivated += 1
        return

    conn.execute(
        _INSERT_COMPANY_PERSON_SQL,
        {
            "id": uuid4().hex if conn.dialect.name == "sqlite" else uuid4(),
            "company_id": company_id,
            "person_id": person_id,
            "phone_normalized": _phone_for_profile(conn, company_id, phone_normalized, user_id, report),
            "user_id": user_id,
            "created_at": now,
        },
    )
    report.profiles_created += 1


def backfill_directory_profiles(conn: Connection, report: BackfillReport) -> None:
    """Every company attachment gets an active, user-linked directory profile."""
    now = datetime.now(timezone.utc)
    for user_id, company_id in conn.execute(_ACCESS_PAIRS_SQL).fetchall():
        ensure_directory_profile(conn, user_id, company_id, report, now=now)
