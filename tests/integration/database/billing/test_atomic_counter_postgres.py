"""Integration tests for SqlAlchemyBillingNumberCounterRepository.

Single-thread correctness runs on SQLite.
Concurrent-thread uniqueness test requires Postgres and is marked
@pytest.mark.requires_postgres — skipped automatically on SQLite.
"""

from __future__ import annotations

import os
import threading
from uuid import UUID, uuid4

import pytest

from app.domain.billing.enums import BillingDocumentKind

# ---------------------------------------------------------------------------
# Postgres-skip marker
# ---------------------------------------------------------------------------

requires_postgres = pytest.mark.skipif(
    "postgresql" not in os.getenv("TEST_DATABASE_URL", ""),
    reason="Requires Postgres TEST_DATABASE_URL for SELECT FOR UPDATE concurrency test",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user_pg(session) -> UUID:
    from app.infrastructure.database.models import UserModel

    user = UserModel(
        id=uuid4(),
        email=f"cnt-{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    session.flush()
    session.commit()
    return UUID(str(user.id))


# ---------------------------------------------------------------------------
# Single-thread correctness (SQLite-safe)
# ---------------------------------------------------------------------------


class TestAtomicCounterSingleThread:
    def test_atomic_numbering_single_thread(self, session):
        """Spec #3: counter increments correctly per (user, kind, year)."""
        from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
            SqlAlchemyBillingNumberCounterRepository,
        )
        from app.infrastructure.database.models import UserModel

        user = UserModel(
            id=uuid4(),
            email=f"cnt-st-{uuid4().hex[:8]}@test.com",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        session.flush()
        user_id = UUID(str(user.id))

        repo = SqlAlchemyBillingNumberCounterRepository(session)

        vals = [repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026) for _ in range(5)]
        assert vals == [1, 2, 3, 4, 5]

    def test_new_year_restarts_at_one(self, session):
        """New year → counter starts at 1 again."""
        from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
            SqlAlchemyBillingNumberCounterRepository,
        )
        from app.infrastructure.database.models import UserModel

        user = UserModel(
            id=uuid4(),
            email=f"cnt-yr-{uuid4().hex[:8]}@test.com",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        session.flush()
        user_id = UUID(str(user.id))

        repo = SqlAlchemyBillingNumberCounterRepository(session)
        v2025 = repo.next_value(user_id, BillingDocumentKind.DEVIS, 2025)
        v2026 = repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        # Both start at 1 — independent keys
        assert v2025 == 1
        assert v2026 == 1

    def test_separate_by_kind(self, session):
        from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
            SqlAlchemyBillingNumberCounterRepository,
        )
        from app.infrastructure.database.models import UserModel

        user = UserModel(
            id=uuid4(),
            email=f"cnt-kd-{uuid4().hex[:8]}@test.com",
            password_hash="x",
            is_active=True,
        )
        session.add(user)
        session.flush()
        user_id = UUID(str(user.id))

        repo = SqlAlchemyBillingNumberCounterRepository(session)
        d1 = repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        f1 = repo.next_value(user_id, BillingDocumentKind.FACTURE, 2026)
        d2 = repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        assert d1 == 1
        assert f1 == 1  # independent
        assert d2 == 2


# ---------------------------------------------------------------------------
# Concurrent uniqueness — Postgres only
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.requires_postgres
def test_atomic_numbering_concurrent_postgres():
    """Spec #4: N parallel threads → no duplicate (user_id, kind, document_number).

    Each thread opens its own SQLAlchemy session + transaction, calls next_value,
    commits, then records the sequence number it received. At the end we assert
    all N values are unique.

    Uses the real Postgres DB (TEST_DATABASE_URL env var). Skipped on SQLite.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.infrastructure.database.models import UserModel
    from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
        SqlAlchemyBillingNumberCounterRepository,
    )

    pg_url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(pg_url, echo=False)
    SessionFactory = sessionmaker(bind=engine)

    N_THREADS = 8

    # Seed a user in a dedicated session so FK constraint is satisfied
    setup_session = SessionFactory()
    user = UserModel(
        id=uuid4(),
        email=f"concurrent-{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
    )
    setup_session.add(user)
    setup_session.commit()
    user_id = UUID(str(user.id))
    setup_session.close()

    results: list[int] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker():
        session = SessionFactory()
        try:
            repo = SqlAlchemyBillingNumberCounterRepository(session)
            val = repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
            session.commit()
            with lock:
                results.append(val)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Worker errors: {errors}"
    assert len(results) == N_THREADS, f"Expected {N_THREADS} results, got {len(results)}"
    assert len(set(results)) == N_THREADS, f"Duplicate document numbers detected! values={sorted(results)}"
    assert sorted(results) == list(range(1, N_THREADS + 1))


# ---------------------------------------------------------------------------
# bump_to_at_least — phase 02
# ---------------------------------------------------------------------------


class TestBumpToAtLeast:
    """Phase 02 — bump_to_at_least semantics (SQLite path)."""

    def _make_repo(self, session):
        from app.infrastructure.database.repositories.sqlalchemy_billing_number_counter_repository import (
            SqlAlchemyBillingNumberCounterRepository,
        )

        return SqlAlchemyBillingNumberCounterRepository(session)

    def test_bump_inserts_absent_row(self, session):
        """Absent counter row → insert with next_value = value + 1."""
        repo = self._make_repo(session)
        company_id = uuid4()
        result = repo.bump_to_at_least(company_id, BillingDocumentKind.FACTURE, 2025, 7)
        # next_value stored should be 8 (= value + 1)
        assert result == 8
        # next call to next_value should return 8
        val = repo.next_value(company_id, BillingDocumentKind.FACTURE, 2025)
        assert val == 8

    def test_bump_higher_value_updates(self, session):
        """Existing counter with next_value=3, bump to 7 → next_value becomes 8."""
        repo = self._make_repo(session)
        company_id = uuid4()
        # seed counter to next_value=3 (claimed 1, 2; stored 3)
        repo.next_value(company_id, BillingDocumentKind.DEVIS, 2025)  # → 1, next=2
        repo.next_value(company_id, BillingDocumentKind.DEVIS, 2025)  # → 2, next=3
        result = repo.bump_to_at_least(company_id, BillingDocumentKind.DEVIS, 2025, 7)
        assert result == 8

    def test_bump_lower_value_does_not_regress(self, session):
        """bump_to_at_least(5) after counter is at 8 → counter stays at 8."""
        repo = self._make_repo(session)
        company_id = uuid4()
        # seed counter to next_value=8
        repo.bump_to_at_least(company_id, BillingDocumentKind.FACTURE, 2026, 7)
        # try to bump down
        result = repo.bump_to_at_least(company_id, BillingDocumentKind.FACTURE, 2026, 3)
        assert result == 8  # unchanged

    def test_bump_then_next_value_continues_from_bumped(self, session):
        """After bump(5), next_value() returns 6 (not 1)."""
        repo = self._make_repo(session)
        company_id = uuid4()
        repo.bump_to_at_least(company_id, BillingDocumentKind.FACTURE, 2025, 5)
        val = repo.next_value(company_id, BillingDocumentKind.FACTURE, 2025)
        assert val == 6
