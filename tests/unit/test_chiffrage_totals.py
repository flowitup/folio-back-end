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
from app.domain.entities.chiffrage_store import ChiffrageStore


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


# ---------------------------------------------------------------------------
# Per-shop baskets
# ---------------------------------------------------------------------------


def _shop(project_id, name, position=1000):
    return ChiffrageStore.create(project_id=project_id, name=name, position=position)


def _basket_world(articles, stores):
    """Build a one-poste tree from (quantity, [(store, price, tva)]) tuples."""
    project_id = uuid4()
    poste = ChiffragePoste.create(project_id=project_id, name="Plomberie", position=1000)
    built = []
    quotes_by_article = {}
    for index, (quantity, offers) in enumerate(articles):
        article = ChiffrageArticle.create(
            poste_id=poste.id, name=f"Item {index}", quantity=Decimal(quantity), position=1000 * (index + 1), unit="u"
        )
        built.append(article)
        quotes_by_article[article.id] = [
            ChiffrageQuote.create(
                article_id=article.id,
                unit_price_ht=Decimal(price),
                tva_rate=Decimal(tva),
                store_id=store.id,
                supplier_name=store.name,
            )
            for store, price, tva in offers
        ]
    tree = build_tree_response(project_id, [poste], {poste.id: built}, quotes_by_article, stores)
    return tree, built


class TestStoreBaskets:
    """What a whole shopping run costs at each shop."""

    def test_a_shop_that_prices_everything_totals_the_lines(self):
        pid = uuid4()
        lm = _shop(pid, "Leroy Merlin")
        tree, _ = _basket_world([("3", [(lm, "10.00", "20")]), ("2", [(lm, "5.00", "20")])], [lm])

        basket = tree.store_baskets[0]
        assert basket.store_id == str(lm.id)
        assert basket.basket_ht == 40.0  # 3x10 + 2x5
        assert basket.basket_ttc == 48.0
        assert basket.covers_all is True
        assert basket.missing_article_ids == []

    def test_a_partial_shop_reports_what_it_cannot_supply(self):
        """The trap: a cheap-looking total that silently skips half the list."""
        pid = uuid4()
        lm = _shop(pid, "Leroy Merlin")
        pp = _shop(pid, "Point P", position=2000)
        tree, articles = _basket_world(
            [("3", [(lm, "10.00", "20"), (pp, "12.00", "20")]), ("2", [(pp, "5.00", "20")])],
            [lm, pp],
        )
        baskets = {b.store_id: b for b in tree.store_baskets}

        partial = baskets[str(lm.id)]
        assert partial.basket_ht == 30.0
        assert partial.covers_all is False
        assert partial.priced_article_count == 1
        assert partial.total_article_count == 2
        assert partial.missing_article_ids == [str(articles[1].id)]

        full = baskets[str(pp.id)]
        assert full.covers_all is True
        assert full.basket_ht == 46.0

    def test_a_complete_shop_outranks_a_cheaper_incomplete_one(self):
        """Ordering must never put a partial basket on top, however cheap."""
        pid = uuid4()
        cheap = _shop(pid, "Cheap But Partial")
        complete = _shop(pid, "Complete", position=2000)
        tree, _ = _basket_world(
            [("1", [(cheap, "1.00", "20"), (complete, "50.00", "20")]), ("1", [(complete, "50.00", "20")])],
            [cheap, complete],
        )
        assert tree.store_baskets[0].store_id == str(complete.id)
        assert tree.store_baskets[0].covers_all is True
        assert tree.store_baskets[1].basket_ht < tree.store_baskets[0].basket_ht

    def test_a_shop_with_no_price_at_all_is_still_listed(self):
        """Absent would read as 'no data'; 0 of 2 is the fact the user needs."""
        pid = uuid4()
        used = _shop(pid, "Used")
        unused = _shop(pid, "Never Priced", position=2000)
        tree, _ = _basket_world([("1", [(used, "10.00", "20")]), ("1", [(used, "10.00", "20")])], [used, unused])

        empty = next(b for b in tree.store_baskets if b.store_id == str(unused.id))
        assert empty.basket_ht == 0.0
        assert empty.priced_article_count == 0
        assert empty.covers_all is False

    def test_the_cheapest_offer_at_one_shop_wins_within_that_shop(self):
        """A chain listing two references for one item: the buyer pays the lower."""
        pid = uuid4()
        lm = _shop(pid, "Leroy Merlin")
        tree, _ = _basket_world([("2", [(lm, "9.00", "20"), (lm, "7.50", "20")])], [lm])
        assert tree.store_baskets[0].basket_ht == 15.0

    def test_a_basket_adds_up_the_same_way_the_rows_do(self):
        """Quantize per line, then sum — never round a full-precision total."""
        pid = uuid4()
        lm = _shop(pid, "Leroy Merlin")
        tree, _ = _basket_world([("3", [(lm, "0.005", "20")]), ("3", [(lm, "0.005", "20")])], [lm])
        # 3 x 0.005 = 0.015 -> 0.02 per line, twice = 0.04 (not 0.03).
        assert tree.store_baskets[0].basket_ht == 0.04

    def test_each_section_gets_its_own_basket(self):
        pid = uuid4()
        lm = ChiffrageStore.create(project_id=pid, name="Leroy Merlin", position=1000)
        plomberie = ChiffragePoste.create(project_id=pid, name="Plomberie", position=1000)
        elec = ChiffragePoste.create(project_id=pid, name="Électricité", position=2000)
        a1 = ChiffrageArticle.create(poste_id=plomberie.id, name="Tap", quantity=Decimal("1"), position=1000, unit="u")
        a2 = ChiffrageArticle.create(poste_id=elec.id, name="Spot", quantity=Decimal("4"), position=1000, unit="u")
        quotes = {
            a1.id: [
                ChiffrageQuote.create(
                    article_id=a1.id, unit_price_ht=Decimal("20"), tva_rate=Decimal("20"), store_id=lm.id
                )
            ],
            a2.id: [
                ChiffrageQuote.create(
                    article_id=a2.id, unit_price_ht=Decimal("5"), tva_rate=Decimal("20"), store_id=lm.id
                )
            ],
        }
        tree = build_tree_response(pid, [plomberie, elec], {plomberie.id: [a1], elec.id: [a2]}, quotes, [lm])

        assert tree.postes[0].store_baskets[0].basket_ht == 20.0
        assert tree.postes[1].store_baskets[0].basket_ht == 20.0
        assert tree.store_baskets[0].basket_ht == 40.0
        assert tree.store_baskets[0].covers_all is True
