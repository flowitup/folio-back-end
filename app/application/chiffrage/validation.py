"""Shared input validation and ownership guards for chiffrage use-cases.

Ownership guards exist because every write route carries the project id in the
URL while nested entities are addressed by their own id. Without an explicit
check, a caller authorised on project A could mutate a poste of project B by
guessing its id — the URL would look legitimate to the permission decorator.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional
from uuid import UUID

from app.application.chiffrage.exceptions import (
    ArticleNotFoundError,
    InvalidChiffrageInputError,
    LibraryProductNotFoundError,
    LibrarySupplierNotFoundError,
    PosteNotFoundError,
    QuoteNotFoundError,
    RoomNotFoundError,
    StoreNotFoundError,
)
from app.application.chiffrage.ports import ChiffrageRepositoryPort
from app.application.chiffrage.units import PRESET_UNITS
from app.domain.entities.chiffrage_store import ChiffrageStore

MAX_POSTE_NAME = 120
MAX_ARTICLE_NAME = 200
MAX_UNIT_SYMBOL = 16
MAX_ROOM_NAME = 120
MAX_STORE_NAME = 160
MAX_STORE_ADDRESS = 500
MAX_STORE_WEBSITE = 500
MAX_SUPPLIER_NAME = 120
MAX_TVA_RATE = Decimal("100")
# Scales of the quote columns: Numeric(12, 4) unit price, Numeric(5, 2) VAT rate.
PRICE_STEP = Decimal("0.0001")
TVA_RATE_STEP = Decimal("0.01")


def clean_name(value: Optional[str], *, field: str, max_length: int) -> str:
    """Strip and validate a required free-text name; None counts as empty."""
    cleaned = (value or "").strip()
    if not cleaned:
        raise InvalidChiffrageInputError(f"{field} cannot be empty.")
    if len(cleaned) > max_length:
        raise InvalidChiffrageInputError(f"{field} cannot exceed {max_length} characters.")
    return cleaned


def clean_optional_text(value: Optional[str]) -> Optional[str]:
    """Normalise optional free text: blank becomes None."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def validate_quantity(value: Decimal) -> Decimal:
    """Reject negative quantities — a costing line cannot need less than nothing."""
    if value < 0:
        raise InvalidChiffrageInputError("Quantity cannot be negative.")
    return value


def validate_price(value: Decimal) -> Decimal:
    """Reject negative unit prices; round to the column's 4 decimals.

    Rounded here rather than by the database so the saved quote the API sends
    back is the one stored, not the extra decimals the column drops.
    """
    if value < 0:
        raise InvalidChiffrageInputError("Unit price cannot be negative.")
    return value.quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def validate_tva_rate(value: Decimal) -> Decimal:
    """Reject VAT rates outside 0-100; round to the column's 2 decimals (see validate_price)."""
    if value < 0 or value > MAX_TVA_RATE:
        raise InvalidChiffrageInputError("VAT rate must be between 0 and 100.")
    return value.quantize(TVA_RATE_STEP, rounding=ROUND_HALF_UP)


def validate_unit(
    repo: ChiffrageRepositoryPort,
    project_id: UUID,
    unit: Optional[str],
) -> Optional[str]:
    """Ensure the unit is a preset or one of the project's custom units.

    This is what makes the front-end dropdown authoritative: without it, a
    direct API call could store any arbitrary string and the select would
    silently show a value it cannot offer.
    """
    if unit is None:
        return None
    cleaned = unit.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_UNIT_SYMBOL:
        raise InvalidChiffrageInputError(f"Unit cannot exceed {MAX_UNIT_SYMBOL} characters.")
    if cleaned in PRESET_UNITS:
        return cleaned
    if repo.unit_exists(project_id, cleaned):
        return cleaned
    raise InvalidChiffrageInputError(f"Unknown unit '{cleaned}' for this project.")


def require_supplier(
    store_id: Optional[UUID],
    supplier_id: Optional[UUID],
    supplier_name: Optional[str],
) -> Optional[str]:
    """A quote must say where its price comes from.

    ``store_id`` is the one that makes prices comparable; the other two remain
    accepted so library-linked quotes and older clients keep working.
    """
    cleaned = clean_optional_text(supplier_name)
    if store_id is None and supplier_id is None and cleaned is None:
        raise InvalidChiffrageInputError("A quote needs a store_id, a supplier_id or a supplier_name.")
    return cleaned


def store_name_snapshot(store: ChiffrageStore) -> str:
    """The shop's name as a quote's readable ``supplier_name`` snapshot.

    A price recorded only against a shop must still say where it came from once
    the shop is detached (shop deleted, project deleted): the supplier check
    constraint needs a store, a supplier or a name. Shop names may be longer
    than the supplier_name column, hence the cut.
    """
    return store.name[:MAX_SUPPLIER_NAME]


def owned_poste(repo: ChiffrageRepositoryPort, poste_id: UUID, project_id: UUID):
    """Load a poste, refusing ids that belong to another project."""
    poste = repo.find_poste(poste_id)
    if poste is None or poste.project_id != project_id:
        raise PosteNotFoundError(f"Poste {poste_id} not found in project {project_id}.")
    return poste


def owned_room(repo: ChiffrageRepositoryPort, room_id: UUID, project_id: UUID):
    """Load a room, refusing ids that belong to another project."""
    room = repo.find_room(room_id)
    if room is None or room.project_id != project_id:
        raise RoomNotFoundError(f"Room {room_id} not found in project {project_id}.")
    return room


def owned_store(repo: ChiffrageRepositoryPort, store_id: UUID, project_id: UUID):
    """Load a store, refusing ids that belong to another project."""
    store = repo.find_store(store_id)
    if store is None or repo.project_id_for_store(store_id) != project_id:
        raise StoreNotFoundError(f"Store {store_id} not found in project {project_id}.")
    return store


def owned_article(repo: ChiffrageRepositoryPort, article_id: UUID, project_id: UUID):
    """Load an article, refusing ids that belong to another project."""
    article = repo.find_article(article_id)
    if article is None or repo.project_id_for_article(article_id) != project_id:
        raise ArticleNotFoundError(f"Article {article_id} not found in project {project_id}.")
    return article


def company_library_refs(
    repo: ChiffrageRepositoryPort,
    project_id: UUID,
    *,
    supplier_id: Optional[UUID] = None,
    library_product_id: Optional[UUID] = None,
) -> None:
    """Refuse a library supplier or product that the project's company does not own.

    The bibliothèque is company-scoped. Without this check a quote could point at
    another company's product, and the tree would then reveal that the product
    exists and has an image; an unknown id would only fail at the foreign key.
    """
    if supplier_id is None and library_product_id is None:
        return
    company_id = repo.company_id_for_project(project_id)
    if supplier_id is not None and (company_id is None or repo.company_id_for_supplier(supplier_id) != company_id):
        raise LibrarySupplierNotFoundError(f"Supplier {supplier_id} not found in this project's company.")
    if library_product_id is not None and (
        company_id is None or repo.company_id_for_library_product(library_product_id) != company_id
    ):
        raise LibraryProductNotFoundError(f"Library product {library_product_id} not found in this project's company.")


def owned_quote(repo: ChiffrageRepositoryPort, quote_id: UUID, project_id: UUID):
    """Load a quote, refusing ids that belong to another project."""
    quote = repo.find_quote(quote_id)
    if quote is None or repo.project_id_for_quote(quote_id) != project_id:
        raise QuoteNotFoundError(f"Quote {quote_id} not found in project {project_id}.")
    return quote
