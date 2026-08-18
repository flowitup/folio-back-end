"""Unit tests for chiffrage price resolution and total assembly.

Pure DTO-layer tests: no database, no Flask. These pin the arithmetic that the
whole feature exists to produce.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.chiffrage.dtos import (
    SOURCE_CHEAPEST,
    SOURCE_NONE,
    SOURCE_SELECTED,
    build_tree_response,
    resolve_effective_quote,
)
from app.domain.entities.chiffrage_article import ChiffrageArticle
from app.domain.entities.chiffrage_poste import ChiffragePoste
from app.domain.entities.chiffrage_quote import ChiffrageQuote


def _quote(article_id, price, tva="20", selected=False, supplier="S"):
    q = ChiffrageQuote.create(
        article_id=article_id,
        unit_price_ht=Decimal(price),
        tva_rate=Decimal(tva),
        supplier_name=supplier,
    )
    return q.with_selection(True) if selected else q


def _world(quantity="12", quotes=()):
    project_id = uuid4()
    poste = ChiffragePoste.create(project_id=project_id, name="Lumière", position=1000)
    article = ChiffrageArticle.create(
        poste_id=poste.id, name="Spot", quantity=Decimal(quantity), position=1000, unit="u"
    )
    quote_list = [q(article.id) for q in quotes]
    tree = build_tree_response(project_id, [poste], {poste.id: [article]}, {article.id: quote_list})
    return tree, tree.postes[0].articles[0]


class TestEffectiveQuoteResolution:
    def test_no_quotes_resolves_to_none(self):
        assert resolve_effective_quote([]) == (None, SOURCE_NONE)

    def test_cheapest_wins_when_nothing_is_retained(self):
        aid = uuid4()
        dear, cheap = _quote(aid, "12.40"), _quote(aid, "10.75")
        picked, source = resolve_effective_quote([dear, cheap])
        assert picked is cheap and source == SOURCE_CHEAPEST

    def test_explicit_pick_beats_a_cheaper_offer(self):
        """Choosing a dearer supplier (site delivery, lead time) must stick."""
        aid = uuid4()
        cheap, retained = _quote(aid, "10.75"), _quote(aid, "11.90", selected=True)
        picked, source = resolve_effective_quote([cheap, retained])
        assert picked is retained and source == SOURCE_SELECTED

    def test_price_ties_resolve_deterministically_by_creation(self):
        """Otherwise the total would flap between reloads on equal prices."""
        aid = uuid4()
        first, second = _quote(aid, "10.00", supplier="A"), _quote(aid, "10.00", supplier="B")
        picked, _ = resolve_effective_quote([second, first])
        assert picked.created_at <= second.created_at


class TestLineTotals:
    def test_line_total_is_quantity_times_unit_price(self):
        _, article = _world("12", [lambda a: _quote(a, "11.90", selected=True)])
        assert article.total_ht == 142.80
        assert article.total_ttc == 171.36

    @pytest.mark.parametrize(
        "tva,expected_ttc",
        [("20", 171.36), ("10", 157.08), ("5.5", 150.65)],
        ids=["standard", "renovation-10", "renovation-5.5"],
    )
    def test_vat_is_applied_per_line_not_globally(self, tva, expected_ttc):
        _, article = _world("12", [lambda a: _quote(a, "11.90", tva=tva)])
        assert article.total_ht == 142.80
        assert article.total_ttc == expected_ttc

    def test_unpriced_article_contributes_nothing_and_is_counted(self):
        tree, article = _world("3", [])
        assert article.effective_source == SOURCE_NONE
        assert article.total_ht == 0.0
        assert tree.unpriced_article_count == 1

    def test_zero_quantity_is_allowed_and_costs_nothing(self):
        _, article = _world("0", [lambda a: _quote(a, "10.00")])
        assert article.total_ht == 0.0


class TestTotalsAddUp:
    def test_subtotals_and_grand_total_match_the_displayed_lines(self):
        """Rounding each line before summing is what keeps the table honest."""
        project_id = uuid4()
        poste = ChiffragePoste.create(project_id=project_id, name="Lumière", position=1000)
        specs = [("3", "33.33", "10"), ("7", "12.34", "20"), ("11", "5.55", "5.5")]
        articles, quotes = [], {}
        for index, (qty, price, tva) in enumerate(specs):
            article = ChiffrageArticle.create(
                poste_id=poste.id, name=f"A{index}", quantity=Decimal(qty), position=1000 * (index + 1), unit="u"
            )
            articles.append(article)
            quotes[article.id] = [_quote(article.id, price, tva=tva)]

        tree = build_tree_response(project_id, [poste], {poste.id: articles}, quotes)
        rendered = tree.postes[0]

        assert round(sum(a.total_ht for a in rendered.articles), 2) == rendered.subtotal_ht
        assert round(sum(a.total_ttc for a in rendered.articles), 2) == rendered.subtotal_ttc
        assert rendered.subtotal_ht == tree.total_ht
        assert rendered.subtotal_ttc == tree.total_ttc

    def test_totals_span_several_postes(self):
        project_id = uuid4()
        postes, articles_by_poste, quotes_by_article = [], {}, {}
        for index, name in enumerate(("Lumière", "Plomberie")):
            poste = ChiffragePoste.create(project_id=project_id, name=name, position=1000 * (index + 1))
            article = ChiffrageArticle.create(
                poste_id=poste.id, name=name, quantity=Decimal("2"), position=1000, unit="u"
            )
            postes.append(poste)
            articles_by_poste[poste.id] = [article]
            quotes_by_article[article.id] = [_quote(article.id, "10.00")]

        tree = build_tree_response(project_id, postes, articles_by_poste, quotes_by_article)
        assert tree.total_ht == 40.00
        assert round(sum(p.subtotal_ht for p in tree.postes), 2) == tree.total_ht


class TestQuoteEntity:
    def test_ttc_is_derived_never_stored(self):
        q = _quote(uuid4(), "10.75", tva="20")
        assert q.unit_price_ttc == Decimal("10.75") * Decimal("1.20")

    def test_selecting_and_unselecting_flips_the_flag(self):
        q = _quote(uuid4(), "10.00")
        assert q.is_selected is False
        assert q.with_selection(True).is_selected is True
        assert q.with_selection(True).with_selection(False).is_selected is False
