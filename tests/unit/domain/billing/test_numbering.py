"""Unit tests for billing document number formatting."""

from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.numbering import kind_to_token, next_document_number


class TestKindToToken:
    def test_devis_token(self):
        assert kind_to_token(BillingDocumentKind.DEVIS) == "DEV"

    def test_facture_token(self):
        assert kind_to_token(BillingDocumentKind.FACTURE) == "FAC"


class TestNextDocumentNumber:
    def test_with_prefix_devis(self):
        result = next_document_number("FLW", BillingDocumentKind.DEVIS, 2026, 7)
        assert result == "FLW-DEV-2026-007"

    def test_with_prefix_facture(self):
        result = next_document_number("FLW", BillingDocumentKind.FACTURE, 2026, 12)
        assert result == "FLW-FAC-2026-012"

    def test_without_prefix_devis(self):
        result = next_document_number("", BillingDocumentKind.DEVIS, 2026, 1)
        assert result == "DEV-2026-001"

    def test_without_prefix_facture(self):
        result = next_document_number("", BillingDocumentKind.FACTURE, 2026, 12)
        assert result == "FAC-2026-012"

    def test_sequence_zero_padded_3_digits(self):
        """Sequence 1 → 001, 99 → 099, 100 → 100."""
        assert next_document_number("", BillingDocumentKind.DEVIS, 2026, 1).endswith("-001")
        assert next_document_number("", BillingDocumentKind.DEVIS, 2026, 99).endswith("-099")
        assert next_document_number("", BillingDocumentKind.DEVIS, 2026, 100).endswith("-100")

    def test_atomic_numbering_single_thread(self):
        """Counter increments correctly per (user, kind, year).

        This tests the domain helper (not the DB repo). Sequences 1..5
        produce distinct, monotonically increasing document numbers.
        """
        numbers = [next_document_number("PRE", BillingDocumentKind.FACTURE, 2026, seq) for seq in range(1, 6)]
        assert numbers == [
            "PRE-FAC-2026-001",
            "PRE-FAC-2026-002",
            "PRE-FAC-2026-003",
            "PRE-FAC-2026-004",
            "PRE-FAC-2026-005",
        ]
        # All unique
        assert len(set(numbers)) == 5

    def test_new_year_restarts_at_001(self):
        """New year → sequence 1 → last 3 chars are 001."""
        n2025 = next_document_number("", BillingDocumentKind.DEVIS, 2025, 1)
        n2026 = next_document_number("", BillingDocumentKind.DEVIS, 2026, 1)
        assert n2025.endswith("2025-001")
        assert n2026.endswith("2026-001")
        # Year is encoded in the number, so they are different despite same sequence
        assert n2025 != n2026
