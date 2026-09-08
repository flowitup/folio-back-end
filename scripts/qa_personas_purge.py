"""Delete every row belonging to one QA company, in one transaction.

Used by ``scripts/qa_personas.py --delete``. The scope is derived, never
hand-listed: for each table, rows are removed when a foreign key points at the
QA company, one of its projects, one of the three QA users or one of their
`persons` rows. Tables are visited children-first (reverse topological order of
the metadata), so no foreign key is ever violated and nothing needs an
``ON DELETE CASCADE`` to work.

Three guards make this safe to run against production:

* the company must be named ``Folio QA …`` — anything else is refused;
* each named user must be attached to that company;
* the QA company must be their ONLY attachment. A user who also belongs to a
  real company is refused, not deleted: rows of theirs could then live outside
  the QA scope.

``dry_run=True`` counts the same rows and rolls back, so the exact blast radius
can be read before anything is deleted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

_QA_COMPANY_PREFIX = "Folio QA "


def id_sql(column: str) -> str:
    """SQL normalising an id column to comparable text.

    Postgres stores UUIDs as `uuid` (dashed text), SQLite as 32 hex chars;
    the same id therefore has two textual forms. Comparing the normalised
    form works on both. It also defeats every index — acceptable here: this
    is a one-shot script over a handful of rows.
    """
    return f"REPLACE(LOWER(CAST({column} AS TEXT)), '-', '')"


def id_param(value: Any) -> str:
    """The same normalisation, for the Python side of a comparison."""
    return str(value).replace("-", "").lower()


def _scalars(session, sql: str, params: dict[str, Any]) -> list[Any]:
    return [row[0] for row in session.execute(text(sql), params).fetchall()]


def _resolve_scope(session, company_name: str, emails: list[str]) -> dict[str, list[Any]]:
    """Return the ids the purge is allowed to touch, or raise ValueError."""
    if not company_name.startswith(_QA_COMPANY_PREFIX):
        raise ValueError(f"refusing to purge {company_name!r}: not a {_QA_COMPANY_PREFIX.strip()!r} company")

    company_ids = _scalars(session, "SELECT id FROM companies WHERE legal_name = :name", {"name": company_name})
    if not company_ids:
        raise ValueError(f"no company named {company_name!r}")
    if len(company_ids) > 1:
        raise ValueError(f"{len(company_ids)} companies named {company_name!r} — refusing to guess")
    company_id = company_ids[0]

    user_ids: list[Any] = []
    for email in emails:
        rows = _scalars(session, "SELECT id FROM users WHERE email = :email", {"email": email.lower()})
        if not rows:
            continue
        user_id = rows[0]
        attachments = _scalars(
            session,
            f"SELECT company_id FROM user_company_access WHERE {id_sql('user_id')} = :uid",
            {"uid": id_param(user_id)},
        )
        if not any(id_param(cid) == id_param(company_id) for cid in attachments):
            raise ValueError(f"refusing to delete {email!r}: not attached to {company_name!r}")
        if len(attachments) > 1:
            raise ValueError(
                f"refusing to delete {email!r}: attached to {len(attachments)} companies, "
                f"not only {company_name!r} — this is not a QA-only account"
            )
        user_ids.append(user_id)

    project_ids = _scalars(
        session,
        f"SELECT id FROM projects WHERE {id_sql('company_id')} = :cid",
        {"cid": id_param(company_id)},
    )
    person_ids: list[Any] = []
    for user_id in user_ids:
        person_ids.extend(
            _scalars(
                session,
                f"SELECT id FROM persons WHERE {id_sql('user_id')} = :uid",
                {"uid": id_param(user_id)},
            )
        )
    return {
        "companies": [company_id],
        "users": user_ids,
        "projects": project_ids,
        "persons": person_ids,
    }


def _match_clauses(table, scope: dict[str, list[Any]]) -> tuple[list[str], dict[str, Any]]:
    """SQL predicates selecting the rows of `table` that belong to the QA scope."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    def add(column_name: str, ids: list[Any]) -> None:
        if not ids:
            return
        prefix = f"p{len(clauses)}"
        placeholders = ", ".join(f":{prefix}_{i}" for i in range(len(ids)))
        clauses.append(f"{id_sql(column_name)} IN ({placeholders})")
        for i, value in enumerate(ids):
            params[f"{prefix}_{i}"] = id_param(value)

    # The row IS one of the scoped entities.
    if table.name in scope:
        add("id", scope[table.name])

    # The row POINTS AT one of them.
    for column in table.columns:
        for fk in column.foreign_keys:
            add(column.name, scope.get(fk.column.table.name, []))

    return clauses, params


def purge_company(company_name: str, emails: list[str], dry_run: bool = False) -> dict[str, int]:
    """Delete the QA company, its people and everything they produced.

    Returns table name → row count for the tables that had rows. Raises
    ValueError when the scope guards reject the request. With `dry_run=True`
    nothing is written: the same rows are counted and the transaction is rolled
    back. Otherwise the whole delete is committed as one transaction.
    """
    from app import db
    from app.infrastructure.database.models import Base

    session = db.session
    scope = _resolve_scope(session, company_name, emails)

    counts: dict[str, int] = {}
    for table in reversed(Base.metadata.sorted_tables):
        clauses, params = _match_clauses(table, scope)
        if not clauses:
            continue
        where = " OR ".join(clauses)
        if dry_run:
            count = session.execute(text(f"SELECT count(*) FROM {table.name} WHERE {where}"), params).scalar() or 0
        else:
            count = session.execute(text(f"DELETE FROM {table.name} WHERE {where}"), params).rowcount
        if count:
            counts[table.name] = count

    if dry_run:
        session.rollback()
    else:
        session.commit()
    return counts
