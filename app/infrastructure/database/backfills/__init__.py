"""One-off data-backfill SQL, extracted from Alembic migrations.

Kept as plain functions taking a SQLAlchemy `Connection`/`Session` so the
exact statements a migration runs can also be exercised by a fast unit test
against the SQLite test DB, without needing a real Alembic upgrade or a
Postgres connection.
"""
