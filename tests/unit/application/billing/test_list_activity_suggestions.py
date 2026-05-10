"""Unit tests for ListActivitySuggestionsUseCase.

Phase 04 — covers frequency ranking, category + q filters, last_* hints,
user-scoping, and empty store.  Integration-level SQLite path via the
in-memory repo's aggregate_item_suggestions.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.list_activity_suggestions_usecase import ListActivitySuggestionsUseCase
from app.domain.billing.value_objects import BillingDocumentItem

from tests.unit.application.billing.conftest import (
    InMemoryBillingDocumentRepository,
    make_doc,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_uc(doc_repo):
    return ListActivitySuggestionsUseCase(doc_repo=doc_repo)


def _item(desc: str, cat: str | None = None, price: str = "100", vat: str = "10") -> BillingDocumentItem:
    return BillingDocumentItem(
        description=desc,
        quantity=Decimal("1"),
        unit_price=Decimal(price),
        vat_rate=Decimal(vat),
        category=cat,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListActivitySuggestionsUseCase:
    def test_empty_store_returns_empty(self):
        repo = InMemoryBillingDocumentRepository()
        uc = _make_uc(repo)
        result = uc.execute(user_id=uuid4())
        assert result.categories == []
        assert result.suggestions == []

    def test_frequency_ranking(self):
        """Two Toiture items, one Menuiserie → Toiture first."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()

        doc1 = make_doc(
            user_id,
            items=(
                _item("Dépose de la toiture existante", cat="Toiture"),
                _item("Pose porte", cat="Menuiserie"),
            ),
        )
        doc2 = make_doc(
            user_id,
            doc_number="FAC-2026-002",
            items=(_item("Dépose de la toiture existante", cat="Toiture"),),
        )
        repo.save(doc1)
        repo.save(doc2)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id)

        # Toiture/Dépose appears 2× → must be first
        assert result.suggestions[0].description == "Dépose de la toiture existante"
        assert result.suggestions[0].frequency == 2
        assert result.suggestions[1].description == "Pose porte"
        assert result.suggestions[1].frequency == 1

    def test_category_filter_excludes_others(self):
        """category=Toiture → only Toiture items returned."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()
        doc = make_doc(
            user_id,
            items=(
                _item("Dépose toiture", cat="Toiture"),
                _item("Pose porte", cat="Menuiserie"),
            ),
        )
        repo.save(doc)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id, category="Toiture")
        descs = [s.description for s in result.suggestions]
        assert "Dépose toiture" in descs
        assert "Pose porte" not in descs

    def test_q_prefix_filter_case_insensitive(self):
        """q='dép' matches 'Dépose' case-insensitively."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()
        doc = make_doc(
            user_id,
            items=(
                _item("Dépose de la toiture existante", cat="Toiture"),
                _item("Pose porte", cat="Menuiserie"),
            ),
        )
        repo.save(doc)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id, q="dép")
        descs = [s.description for s in result.suggestions]
        assert "Dépose de la toiture existante" in descs
        assert "Pose porte" not in descs

    def test_user_scoping_no_cross_user_leak(self):
        """Other user's items must not appear in results."""
        user_a = uuid4()
        user_b = uuid4()
        repo = InMemoryBillingDocumentRepository()

        doc_a = make_doc(user_a, items=(_item("Service A", cat="Cat"),))
        doc_b = make_doc(user_b, doc_number="DEV-2026-B01", items=(_item("Service B", cat="Cat"),))
        repo.save(doc_a)
        repo.save(doc_b)

        uc = _make_uc(repo)
        result_a = uc.execute(user_id=user_a)
        descs_a = [s.description for s in result_a.suggestions]
        assert "Service A" in descs_a
        assert "Service B" not in descs_a

    def test_categories_list_distinct_and_sorted(self):
        """categories list is sorted alphabetically."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()
        doc = make_doc(
            user_id,
            items=(
                _item("X", cat="Toiture"),
                _item("Y", cat="Menuiserie"),
                _item("Z", cat="Carrelage"),
            ),
        )
        repo.save(doc)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id)
        names = [c.name for c in result.categories]
        assert names == sorted(names)

    def test_last_unit_price_hint(self):
        """last_unit_price reflects the item's unit_price as string."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()
        doc = make_doc(
            user_id,
            items=(_item("Pose", cat="Toiture", price="900.00"),),
        )
        repo.save(doc)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id)
        assert result.suggestions[0].last_unit_price == "900.00"

    def test_limit_respected(self):
        """limit=1 returns at most 1 suggestion."""
        user_id = uuid4()
        repo = InMemoryBillingDocumentRepository()
        doc = make_doc(
            user_id,
            items=(
                _item("A", cat="Cat"),
                _item("B", cat="Cat"),
                _item("C", cat="Cat"),
            ),
        )
        repo.save(doc)

        uc = _make_uc(repo)
        result = uc.execute(user_id=user_id, limit=1)
        assert len(result.suggestions) == 1

    def test_invalid_limit_raises(self):
        repo = InMemoryBillingDocumentRepository()
        uc = _make_uc(repo)
        with pytest.raises(ValueError):
            uc.execute(user_id=uuid4(), limit=0)
        with pytest.raises(ValueError):
            uc.execute(user_id=uuid4(), limit=101)
