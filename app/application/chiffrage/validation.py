"""Shared input validation and ownership guards for chiffrage use-cases.

Ownership guards exist because every write route carries the project id in the
URL while nested entities are addressed by their own id. Without an explicit
check, a caller authorised on project A could mutate a poste of project B by
guessing its id — the URL would look legitimate to the permission decorator.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.chiffrage.exceptions import (
    ArticleNotFoundError,
    InvalidChiffrageInputError,
    PosteNotFoundError,
    QuoteNotFoundError,
    StoreNotFoundError,
)
from app.application.chiffrage.ports import ChiffrageRepositoryPort
from app.application.chiffrage.units import PRESET_UNITS

MAX_POSTE_NAME = 120
MAX_ARTICLE_NAME = 200
MAX_UNIT_SYMBOL = 16
MAX_STORE_NAME = 160
MAX_STORE_ADDRESS = 500
MAX_STORE_WEBSITE = 500
MAX_TVA_RATE = Decimal("100")


def clean_name(value: str, *, field: str, max_length: int) -> str:
    """Strip and validate a required free-text name."""
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
    """Reject negative unit prices."""
    if value < 0:
        raise InvalidChiffrageInputError("Unit price cannot be negative.")
    return value


def validate_tva_rate(value: Decimal) -> Decimal:
    """Reject VAT rates outside 0-100."""
    if value < 0 or value > MAX_TVA_RATE:
        raise InvalidChiffrageInputError("VAT rate must be between 0 and 100.")
    return value


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


def require_supplier(supplier_id: Optional[UUID], supplier_name: Optional[str]) -> Optional[str]:
    """A quote must name its fournisseur one way or the other."""
    cleaned = clean_optional_text(supplier_name)
    if supplier_id is None and cleaned is None:
        raise InvalidChiffrageInputError("A quote needs either a supplier_id or a supplier_name.")
    return cleaned


def owned_poste(repo: ChiffrageRepositoryPort, poste_id: UUID, project_id: UUID):
    """Load a poste, refusing ids that belong to another project."""
    poste = repo.find_poste(poste_id)
    if poste is None or poste.project_id != project_id:
        raise PosteNotFoundError(f"Poste {poste_id} not found in project {project_id}.")
    return poste


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


def owned_quote(repo: ChiffrageRepositoryPort, quote_id: UUID, project_id: UUID):
    """Load a quote, refusing ids that belong to another project."""
    quote = repo.find_quote(quote_id)
    if quote is None or repo.project_id_for_quote(quote_id) != project_id:
        raise QuoteNotFoundError(f"Quote {quote_id} not found in project {project_id}.")
    return quote
