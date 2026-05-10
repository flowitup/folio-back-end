"""Unit tests for InMemoryBillingNumberCounterRepository (in-memory fake).

The DB-level concurrent test lives in tests/integration/database/billing/.
"""

from app.domain.billing.enums import BillingDocumentKind


class TestInMemoryCounter:
    def test_first_call_returns_one(self, counter_repo, user_id):
        val = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        assert val == 1

    def test_increments_monotonically(self, counter_repo, user_id):
        vals = [counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026) for _ in range(5)]
        assert vals == [1, 2, 3, 4, 5]

    def test_separate_by_kind(self, counter_repo, user_id):
        """DEVIS and FACTURE counters are independent for the same user+year."""
        devis_1 = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        facture_1 = counter_repo.next_value(user_id, BillingDocumentKind.FACTURE, 2026)
        devis_2 = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        assert devis_1 == 1
        assert facture_1 == 1
        assert devis_2 == 2

    def test_separate_by_year(self, counter_repo, user_id):
        """Counter resets to 1 for a new year (separate key)."""
        v2025 = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2025)
        v2026 = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        assert v2025 == 1
        assert v2026 == 1  # independent counter

    def test_separate_by_user(self, counter_repo, user_id, other_user_id):
        """Each user has independent counters."""
        v1 = counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2026)
        v2 = counter_repo.next_value(other_user_id, BillingDocumentKind.DEVIS, 2026)
        assert v1 == 1
        assert v2 == 1

    def test_all_unique_no_duplicates(self, counter_repo, user_id):
        """No duplicate sequence numbers produced."""
        vals = [counter_repo.next_value(user_id, BillingDocumentKind.FACTURE, 2026) for _ in range(20)]
        assert len(vals) == len(set(vals))


class TestInMemoryBumpToAtLeast:
    """Phase 02 — bump_to_at_least on in-memory repo."""

    def test_bump_absent_row(self, counter_repo, user_id):
        result = counter_repo.bump_to_at_least(user_id, BillingDocumentKind.FACTURE, 2025, 5)
        assert result == 6
        # next call should return 6
        assert counter_repo.next_value(user_id, BillingDocumentKind.FACTURE, 2025) == 6

    def test_bump_higher_than_existing(self, counter_repo, user_id):
        counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2025)  # consumes 1
        counter_repo.next_value(user_id, BillingDocumentKind.DEVIS, 2025)  # consumes 2
        # next_value is now 3; bump to at least 7
        result = counter_repo.bump_to_at_least(user_id, BillingDocumentKind.DEVIS, 2025, 7)
        assert result == 8

    def test_bump_lower_does_not_regress(self, counter_repo, user_id):
        counter_repo.bump_to_at_least(user_id, BillingDocumentKind.DEVIS, 2025, 7)
        result = counter_repo.bump_to_at_least(user_id, BillingDocumentKind.DEVIS, 2025, 3)
        assert result == 8
