"""Backfill for `persons.phone_normalized` and `persons.user_id` (Phase 2).

Two independent backfills, run in order by the migration:

1. `user_id` — one user maps to at most one person. Derived from
   `workers.user_id`: for every distinct `(person_id, user_id)` pair among
   worker rows that carry both, link `persons.user_id`. If a single
   `user_id` appears against MORE THAN ONE distinct `person_id` (the user
   was linked to workers under two different Person identities — a data
   quality issue, not something to guess at), that user is logged and
   left unlinked for ops review rather than picking one arbitrarily.

2. `phone_normalized` — computed in Python (not SQL) via
   `app.domain.value_objects.phone_number.normalize_phone`, using the
   region of the company the person is linked to via `company_persons`
   (Phase 2 also creates that table, but it is empty at migration time —
   in practice this pass runs before any `company_persons` rows exist, so
   every person backfills with the 'FR' default). Rows whose `phone` does
   not parse as a valid number are left NULL rather than raising — a
   malformed legacy value must not fail the migration.

Both passes are idempotent: re-running only touches rows still NULL.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.domain.value_objects.phone_number import InvalidPhoneNumberError, normalize_phone

logger = logging.getLogger(__name__)

_DEFAULT_REGION = "FR"

_WORKER_USER_PERSON_PAIRS_SQL = text(
    """
    SELECT DISTINCT user_id, person_id
    FROM workers
    WHERE user_id IS NOT NULL AND person_id IS NOT NULL
    """
)

_SET_PERSON_USER_ID_SQL = text("UPDATE persons SET user_id = :user_id WHERE id = :person_id AND user_id IS NULL")

_PERSON_COMPANY_REGION_SQL = text(
    """
    SELECT c.default_phone_region
    FROM company_persons cp
    JOIN companies c ON c.id = cp.company_id
    WHERE cp.person_id = :person_id
    LIMIT 1
    """
)

_PERSONS_NEEDING_PHONE_NORM_SQL = text(
    "SELECT id, phone FROM persons WHERE phone IS NOT NULL AND phone_normalized IS NULL"
)

_SET_PHONE_NORMALIZED_SQL = text("UPDATE persons SET phone_normalized = :normalized WHERE id = :id")


def backfill_user_id_from_workers(conn: Connection) -> list[UUID]:
    """Link `persons.user_id` from `workers.user_id`, one user → one person.

    Returns the list of `user_id`s left unlinked because they mapped to more
    than one distinct person (ambiguous — needs a human to pick one).
    """
    rows = conn.execute(_WORKER_USER_PERSON_PAIRS_SQL).fetchall()
    by_user: dict[str, set[str]] = {}
    for user_id, person_id in rows:
        by_user.setdefault(str(user_id), set()).add(str(person_id))

    ambiguous: list[UUID] = []
    for user_id_str, person_ids in by_user.items():
        if len(person_ids) > 1:
            ambiguous.append(UUID(user_id_str))
            logger.warning(
                "persons.user_id backfill: user %s maps to %d distinct persons — left unlinked",
                user_id_str,
                len(person_ids),
            )
            continue
        (person_id_str,) = tuple(person_ids)
        conn.execute(_SET_PERSON_USER_ID_SQL, {"user_id": user_id_str, "person_id": person_id_str})
    return ambiguous


def _region_for_person(conn: Connection, person_id) -> str:
    """Return the default phone region of the company `person_id` belongs to, or 'FR'."""
    row = conn.execute(_PERSON_COMPANY_REGION_SQL, {"person_id": str(person_id)}).fetchone()
    if row is None or row[0] is None:
        return _DEFAULT_REGION
    return row[0]


def backfill_phone_normalized(conn: Connection) -> int:
    """Compute `phone_normalized` for every person with a raw `phone` and no normalized form yet.

    Returns the count of rows left NULL because the raw phone did not parse.
    """
    rows = conn.execute(_PERSONS_NEEDING_PHONE_NORM_SQL).fetchall()
    unparseable = 0
    for person_id, raw_phone in rows:
        region = _region_for_person(conn, person_id)
        try:
            normalized = normalize_phone(raw_phone, default_region=region)
        except InvalidPhoneNumberError:
            unparseable += 1
            continue
        conn.execute(_SET_PHONE_NORMALIZED_SQL, {"normalized": normalized, "id": str(person_id)})
    return unparseable


def run_backfill(conn: Connection) -> tuple[list[UUID], int]:
    """Run both passes; return (ambiguous_user_ids, unparseable_phone_count)."""
    ambiguous = backfill_user_id_from_workers(conn)
    unparseable = backfill_phone_normalized(conn)
    return ambiguous, unparseable
