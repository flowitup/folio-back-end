"""Response DTOs and total assembly for the chiffrage application layer.

Rounding contract (mirrors app.application.invoice.dtos.money):
    line HT  = quantize(quantity * unit_price_ht)
    line TTC = quantize(line_HT * (1 + tva_rate / 100))
    poste subtotal = sum of ALREADY-QUANTIZED line totals
    grand total    = sum of poste subtotals

Summing rounded lines (rather than rounding a full-precision sum) is what makes
the displayed rows add up to the displayed subtotal — an accountant reading the
table must never find a one-cent discrepancy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from app.domain.entities.chiffrage_article import ChiffrageArticle
from app.domain.entities.chiffrage_poste import ChiffragePoste
from app.domain.entities.chiffrage_quote import ChiffrageQuote
from app.domain.entities.chiffrage_store import ChiffrageStore

_CENTS = Decimal("0.01")
_HUNDRED = Decimal("100")

# How an article's effective price was chosen.
SOURCE_SELECTED = "selected"
SOURCE_CHEAPEST = "cheapest"
SOURCE_NONE = "none"


def money(value: Decimal) -> float:
    """Quantize a monetary Decimal to cents for serialization.

    Same contract as the invoice module's helper; kept local so the chiffrage
    bounded context does not import another context's DTO module.
    """
    return float(value.quantize(_CENTS, rounding=ROUND_HALF_UP))


def _quantize(value: Decimal) -> Decimal:
    """Round to cents, staying in Decimal for further exact summation."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass
class QuoteResponse:
    id: str
    article_id: str
    supplier_id: Optional[str]
    supplier_name: Optional[str]
    library_product_id: Optional[str]
    unit_price_ht: float
    tva_rate: float
    unit_price_ttc: float
    product_url: Optional[str]
    note: Optional[str]
    is_selected: bool

    @classmethod
    def from_entity(cls, q: ChiffrageQuote) -> "QuoteResponse":
        return cls(
            id=str(q.id),
            article_id=str(q.article_id),
            supplier_id=str(q.supplier_id) if q.supplier_id else None,
            supplier_name=q.supplier_name,
            library_product_id=(str(q.library_product_id) if q.library_product_id else None),
            unit_price_ht=money(q.unit_price_ht),
            tva_rate=float(q.tva_rate),
            unit_price_ttc=money(q.unit_price_ttc),
            product_url=q.product_url,
            note=q.note,
            is_selected=q.is_selected,
        )


@dataclass
class ArticleResponse:
    id: str
    poste_id: str
    name: str
    quantity: float
    unit: Optional[str]
    note: Optional[str]
    position: int
    quotes: list[QuoteResponse] = field(default_factory=list)
    effective_quote_id: Optional[str] = None
    effective_source: str = SOURCE_NONE
    total_ht: float = 0.0
    total_ttc: float = 0.0


@dataclass
class StoreResponse:
    """A shop to visit for this poste."""

    id: str
    poste_id: str
    name: str
    address: Optional[str]
    position: int

    @classmethod
    def from_entity(cls, s: ChiffrageStore) -> "StoreResponse":
        return cls(
            id=str(s.id),
            poste_id=str(s.poste_id),
            name=s.name,
            address=s.address,
            position=s.position,
        )


@dataclass
class PosteResponse:
    id: str
    project_id: str
    name: str
    note: Optional[str]
    position: int
    articles: list[ArticleResponse] = field(default_factory=list)
    stores: list[StoreResponse] = field(default_factory=list)
    subtotal_ht: float = 0.0
    subtotal_ttc: float = 0.0


@dataclass
class ChiffrageTreeResponse:
    project_id: str
    postes: list[PosteResponse] = field(default_factory=list)
    total_ht: float = 0.0
    total_ttc: float = 0.0
    unpriced_article_count: int = 0


def resolve_effective_quote(quotes: list[ChiffrageQuote]) -> tuple[Optional[ChiffrageQuote], str]:
    """Pick the quote that drives an article's budget line.

    Order: the user's explicit pick, else the cheapest offer (so the table is
    never blank while data is still being entered), else nothing.

    Ties on price resolve to the earliest-created quote, keeping the choice
    stable across reloads instead of flapping with dict ordering.
    """
    if not quotes:
        return None, SOURCE_NONE
    for q in quotes:
        if q.is_selected:
            return q, SOURCE_SELECTED
    cheapest = min(quotes, key=lambda q: (q.unit_price_ht, q.created_at))
    return cheapest, SOURCE_CHEAPEST


def _build_article(article: ChiffrageArticle, quotes: list[ChiffrageQuote]) -> tuple[ArticleResponse, Decimal, Decimal]:
    """Assemble one article response plus its exact (HT, TTC) Decimal line totals."""
    effective, source = resolve_effective_quote(quotes)

    if effective is None:
        line_ht = Decimal("0")
        line_ttc = Decimal("0")
    else:
        line_ht = _quantize(article.quantity * effective.unit_price_ht)
        line_ttc = _quantize(line_ht * (Decimal("1") + effective.tva_rate / _HUNDRED))

    response = ArticleResponse(
        id=str(article.id),
        poste_id=str(article.poste_id),
        name=article.name,
        quantity=float(article.quantity),
        unit=article.unit,
        note=article.note,
        position=article.position,
        quotes=[QuoteResponse.from_entity(q) for q in quotes],
        effective_quote_id=str(effective.id) if effective else None,
        effective_source=source,
        total_ht=float(line_ht),
        total_ttc=float(line_ttc),
    )
    return response, line_ht, line_ttc


def build_tree_response(
    project_id: UUID,
    postes: list[ChiffragePoste],
    articles_by_poste: dict[UUID, list[ChiffrageArticle]],
    quotes_by_article: dict[UUID, list[ChiffrageQuote]],
    stores_by_poste: Optional[dict[UUID, list[ChiffrageStore]]] = None,
) -> ChiffrageTreeResponse:
    """Assemble the full chiffrage tree with per-level totals.

    Articles with no quote contribute nothing to the totals but are counted in
    ``unpriced_article_count`` so the UI can warn that the budget is incomplete
    rather than silently under-reporting it.
    """
    poste_responses: list[PosteResponse] = []
    grand_ht = Decimal("0")
    grand_ttc = Decimal("0")
    unpriced = 0

    for poste in postes:
        subtotal_ht = Decimal("0")
        subtotal_ttc = Decimal("0")
        article_responses: list[ArticleResponse] = []

        for article in articles_by_poste.get(poste.id, []):
            quotes = quotes_by_article.get(article.id, [])
            if not quotes:
                unpriced += 1
            article_response, line_ht, line_ttc = _build_article(article, quotes)
            article_responses.append(article_response)
            subtotal_ht += line_ht
            subtotal_ttc += line_ttc

        poste_responses.append(
            PosteResponse(
                id=str(poste.id),
                project_id=str(poste.project_id),
                name=poste.name,
                note=poste.note,
                position=poste.position,
                articles=article_responses,
                stores=[StoreResponse.from_entity(s) for s in (stores_by_poste or {}).get(poste.id, [])],
                subtotal_ht=float(subtotal_ht),
                subtotal_ttc=float(subtotal_ttc),
            )
        )
        grand_ht += subtotal_ht
        grand_ttc += subtotal_ttc

    return ChiffrageTreeResponse(
        project_id=str(project_id),
        postes=poste_responses,
        total_ht=float(grand_ht),
        total_ttc=float(grand_ttc),
        unpriced_article_count=unpriced,
    )


@dataclass
class UnitResponse:
    """A selectable unit symbol. Presets are not rows, hence the nullable id."""

    id: Optional[str]
    symbol: str
    is_preset: bool
