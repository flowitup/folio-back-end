"""M4 — SqlAlchemyAuthzReader is dialect-conditional: direct UUID comparison
on Postgres (index-friendly), REPLACE(LOWER(CAST(...))) normalization on
SQLite only. Exercises the private SQL-building/bind helpers directly (no
real Postgres connection needed — `_cached_dialect_name` is set by hand to
avoid touching `session.get_bind()`).
"""

from __future__ import annotations

from uuid import uuid4

from app.infrastructure.database.repositories.sqlalchemy_authz_reader import SqlAlchemyAuthzReader


def _reader_for_dialect(dialect_name: str) -> SqlAlchemyAuthzReader:
    reader = SqlAlchemyAuthzReader(session=None)  # session unused by the helpers under test
    reader._cached_dialect_name = dialect_name
    return reader


def test_postgres_uses_direct_equality_no_normalization():
    reader = _reader_for_dialect("postgresql")
    assert reader._eq("user_id", "uid") == "user_id = :uid"
    assert "REPLACE" not in reader._eq("user_id", "uid")


def test_sqlite_normalizes_both_sides():
    reader = _reader_for_dialect("sqlite")
    fragment = reader._eq("user_id", "uid")
    assert "REPLACE(LOWER(CAST(user_id AS TEXT))" in fragment
    assert "REPLACE(LOWER(CAST(:uid AS TEXT))" in fragment


def test_postgres_binds_the_uuid_object_itself():
    reader = _reader_for_dialect("postgresql")
    value = uuid4()
    bound = reader._bind_uuid(value)
    assert bound is value


def test_sqlite_binds_the_string_form():
    reader = _reader_for_dialect("sqlite")
    value = uuid4()
    bound = reader._bind_uuid(value)
    assert bound == str(value)
    assert isinstance(bound, str)


def test_dialect_name_is_cached_on_first_resolution():
    """_dialect_name() must not re-query session.get_bind() once resolved."""
    reader = _reader_for_dialect("postgresql")
    # session is None — if _dialect_name() tried to re-resolve it would raise.
    assert reader._dialect_name() == "postgresql"
    assert reader._is_sqlite() is False
