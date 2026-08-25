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
from app.domain.entities.chiffrage_room import ChiffrageRoom
from app.domain.entities.chiffrage_store import ChiffrageStore

_CENTS = Decimal("0.01")
_HUNDRED = Decimal("100")

# How an article's effective price was chosen.
SOURCE_SELECTED = "selected"
SOURCE_CHEAPEST = "cheapest"
SOURCE_NONE = "none"

# Where an article's thumbnail comes from.
IMAGE_OWN = "article"
IMAGE_LIBRARY = "library"


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
    store_id: Optional[str]
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
            store_id=str(q.store_id) if q.store_id else None,
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
    room_id: Optional[str]
    position: int
    quotes: list[QuoteResponse] = field(default_factory=list)
    # None, or {"kind": "article"|"library", "id": "<uuid>"} telling the client
    # which image endpoint to fetch. Bytes are streamed through the API, so the
    # client never needs an object-store URL.
    image_ref: Optional[dict] = None
    effective_quote_id: Optional[str] = None
    effective_source: str = SOURCE_NONE
    total_ht: float = 0.0
    total_ttc: float = 0.0


@dataclass
class RoomResponse:
    """A room of the chantier, shared by every poste."""

    id: str
    name: str
    position: int

    @classmethod
    def from_entity(cls, r: ChiffrageRoom) -> "RoomResponse":
        return cls(id=str(r.id), name=r.name, position=r.position)


@dataclass
class RoomSubtotal:
    """What one room costs inside one poste. None id = articles with no room."""

    room_id: Optional[str]
    subtotal_ht: float
    subtotal_ttc: float
    article_count: int


@dataclass
class StoreResponse:
    """A shop the project buys from."""

    id: str
    project_id: str
    name: str
    address: Optional[str]
    website_url: Optional[str]
    position: int

    @classmethod
    def from_entity(cls, s: ChiffrageStore) -> "StoreResponse":
        return cls(
            id=str(s.id),
            project_id=str(s.project_id),
            name=s.name,
            address=s.address,
            website_url=s.website_url,
            position=s.position,
        )


@dataclass
class StoreBasket:
    """What one shop would cost for a given set of articles.

    ``covers_all`` is not a nicety. A basket that silently skips the articles a
    shop has no price for ranks the *least* complete shop first — price three
    of twenty items and that shop "wins". Every consumer must therefore read
    the coverage alongside the total, and only a shop with ``covers_all`` may
    be presented as the cheapest option.
    """

    store_id: str
    basket_ht: float
    basket_ttc: float
    priced_article_count: int
    total_article_count: int
    missing_article_ids: list[str]
    covers_all: bool


@dataclass
class PosteResponse:
    id: str
    project_id: str
    name: str
    note: Optional[str]
    position: int
    articles: list[ArticleResponse] = field(default_factory=list)
    # What each shop would cost for this section alone.
    store_baskets: list[StoreBasket] = field(default_factory=list)
    # Per-room breakdown inside this poste, so the UI never re-adds money.
    room_subtotals: list[RoomSubtotal] = field(default_factory=list)
    subtotal_ht: float = 0.0
    subtotal_ttc: float = 0.0


@dataclass
class ChiffrageTreeResponse:
    project_id: str
    postes: list[PosteResponse] = field(default_factory=list)
    # The project's room vocabulary, in display order.
    rooms: list[RoomResponse] = field(default_factory=list)
    # The project's shops, declared once and shared by every poste.
    stores: list[StoreResponse] = field(default_factory=list)
    # What each shop would cost for the whole project.
    store_baskets: list[StoreBasket] = field(default_factory=list)
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


def _resolve_image_ref(
    article: ChiffrageArticle,
    effective: Optional[ChiffrageQuote],
    library_with_image: set,
) -> Optional[dict]:
    """Pick the thumbnail to show for an article.

    The article's own photo wins. Failing that, if the retained quote points at
    a library product that has one, borrow it — most finition items come
    straight out of the bibliothèque, so this fills the grid without asking the
    user to upload the same photo twice.
    """
    if article.image_storage_key:
        return {"kind": IMAGE_OWN, "id": str(article.id)}
    if effective is not None and effective.library_product_id in library_with_image:
        return {"kind": IMAGE_LIBRARY, "id": str(effective.library_product_id)}
    return None


def _build_article(
    article: ChiffrageArticle,
    quotes: list[ChiffrageQuote],
    library_with_image: Optional[set] = None,
) -> tuple[ArticleResponse, Decimal, Decimal]:
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
        room_id=str(article.room_id) if article.room_id else None,
        position=article.position,
        quotes=[QuoteResponse.from_entity(q) for q in quotes],
        image_ref=_resolve_image_ref(article, effective, library_with_image or set()),
        effective_quote_id=str(effective.id) if effective else None,
        effective_source=source,
        total_ht=float(line_ht),
        total_ttc=float(line_ttc),
    )
    return response, line_ht, line_ttc


def _room_subtotals(per_room: dict, rooms: Optional[list[ChiffrageRoom]]) -> list[RoomSubtotal]:
    """Order the per-room figures the way the rooms are displayed.

    Rooms follow the project's declared order; articles with no room come last,
    so an incomplete assignment reads as a leftover rather than a first item.
    """
    order = {str(r.id): i for i, r in enumerate(rooms or [])}
    keys = sorted(per_room, key=lambda k: (k is None, order.get(k, len(order))))
    return [
        RoomSubtotal(
            room_id=k,
            subtotal_ht=float(per_room[k][0]),
            subtotal_ttc=float(per_room[k][1]),
            article_count=per_room[k][2],
        )
        for k in keys
    ]


def _build_store_baskets(
    scope: list[tuple[ChiffrageArticle, list[ChiffrageQuote]]],
    stores: list[ChiffrageStore],
) -> list[StoreBasket]:
    """Cost the given articles at each shop, one basket per shop.

    Line totals go through the same quantize-then-sum contract as every other
    figure on the page, so a basket adds up exactly like the item rows do.

    Where a shop has several quotes for one article — a chain sometimes lists
    two references for the same thing — the cheapest of that shop's own quotes
    is used, which is what the buyer would actually pay there.

    Every shop is returned, including ones with no price at all: a shop absent
    from the list would read as "no data", whereas 0 of 12 covered is a fact
    the user needs in order to trust the comparison.
    """
    baskets: list[StoreBasket] = []
    total_articles = len(scope)

    for store in stores:
        store_key = store.id
        basket_ht = Decimal("0")
        basket_ttc = Decimal("0")
        priced = 0
        missing: list[str] = []

        for article, quotes in scope:
            at_store = [q for q in quotes if q.store_id == store_key]
            if not at_store:
                missing.append(str(article.id))
                continue
            best = min(at_store, key=lambda q: (q.unit_price_ht, q.created_at))
            line_ht = _quantize(article.quantity * best.unit_price_ht)
            basket_ht += line_ht
            basket_ttc += _quantize(line_ht * (Decimal("1") + best.tva_rate / _HUNDRED))
            priced += 1

        baskets.append(
            StoreBasket(
                store_id=str(store_key),
                basket_ht=float(basket_ht),
                basket_ttc=float(basket_ttc),
                priced_article_count=priced,
                total_article_count=total_articles,
                missing_article_ids=missing,
                covers_all=(priced == total_articles and total_articles > 0),
            )
        )

    # Cheapest first among shops that cover everything, then by coverage: the
    # ordering the UI renders is therefore already the ordering it should show.
    baskets.sort(key=lambda b: (not b.covers_all, -b.priced_article_count, b.basket_ht))
    return baskets


def build_tree_response(
    project_id: UUID,
    postes: list[ChiffragePoste],
    articles_by_poste: dict[UUID, list[ChiffrageArticle]],
    quotes_by_article: dict[UUID, list[ChiffrageQuote]],
    stores: Optional[list[ChiffrageStore]] = None,
    library_with_image: Optional[set] = None,
    rooms: Optional[list[ChiffrageRoom]] = None,
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
    project_stores = stores or []
    # (article, quotes) for the whole project, reused for the project-wide baskets.
    project_scope: list[tuple[ChiffrageArticle, list[ChiffrageQuote]]] = []

    for poste in postes:
        subtotal_ht = Decimal("0")
        subtotal_ttc = Decimal("0")
        article_responses: list[ArticleResponse] = []
        poste_scope: list[tuple[ChiffrageArticle, list[ChiffrageQuote]]] = []
        # Keyed by room id (None = unassigned) so the per-room figures come
        # from the same quantized line totals as the poste subtotal.
        per_room: dict = {}

        for article in articles_by_poste.get(poste.id, []):
            quotes = quotes_by_article.get(article.id, [])
            poste_scope.append((article, quotes))
            project_scope.append((article, quotes))
            if not quotes:
                unpriced += 1
            article_response, line_ht, line_ttc = _build_article(article, quotes, library_with_image)
            article_responses.append(article_response)
            subtotal_ht += line_ht
            subtotal_ttc += line_ttc

            key = str(article.room_id) if article.room_id else None
            bucket = per_room.setdefault(key, [Decimal("0"), Decimal("0"), 0])
            bucket[0] += line_ht
            bucket[1] += line_ttc
            bucket[2] += 1

        poste_responses.append(
            PosteResponse(
                id=str(poste.id),
                project_id=str(poste.project_id),
                name=poste.name,
                note=poste.note,
                position=poste.position,
                articles=article_responses,
                store_baskets=_build_store_baskets(poste_scope, project_stores),
                room_subtotals=_room_subtotals(per_room, rooms),
                subtotal_ht=float(subtotal_ht),
                subtotal_ttc=float(subtotal_ttc),
            )
        )
        grand_ht += subtotal_ht
        grand_ttc += subtotal_ttc

    return ChiffrageTreeResponse(
        project_id=str(project_id),
        postes=poste_responses,
        rooms=[RoomResponse.from_entity(r) for r in (rooms or [])],
        stores=[StoreResponse.from_entity(s) for s in project_stores],
        store_baskets=_build_store_baskets(project_scope, project_stores),
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
