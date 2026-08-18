"""Store write use-cases: create, update, delete.

A store records *where to go and buy*, which is a different question from the
quotes' *who sells at what price* — a poste typically needs a run across several
shops, so these hang off the poste as a list rather than a single field.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import POSITION_STEP
from app.application.chiffrage.validation import (
    MAX_STORE_ADDRESS,
    MAX_STORE_WEBSITE,
    MAX_STORE_NAME,
    clean_name,
    clean_optional_text,
    owned_poste,
    owned_store,
)
from app.application.chiffrage.exceptions import InvalidChiffrageInputError
from app.domain.entities.chiffrage_store import ChiffrageStore


def _clean_address(value: Optional[str]) -> Optional[str]:
    """Normalise an address, rejecting anything past the column width."""
    cleaned = clean_optional_text(value)
    if cleaned is not None and len(cleaned) > MAX_STORE_ADDRESS:
        raise InvalidChiffrageInputError(f"Store address cannot exceed {MAX_STORE_ADDRESS} characters.")
    return cleaned


def _clean_website(value: Optional[str]) -> Optional[str]:
    """Normalise a website URL. Scheme is not enforced — same as the quote
    product_url, which also stores whatever the user pasted."""
    cleaned = clean_optional_text(value)
    if cleaned is not None and len(cleaned) > MAX_STORE_WEBSITE:
        raise InvalidChiffrageInputError(f"Store website cannot exceed {MAX_STORE_WEBSITE} characters.")
    return cleaned


class CreateStoreUseCase:
    """Append a shop to a poste's list."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        poste_id: UUID,
        name: str,
        address: Optional[str] = None,
        website_url: Optional[str] = None,
    ) -> ChiffrageStore:
        owned_poste(self._repo, poste_id, project_id)
        store = ChiffrageStore.create(
            poste_id=poste_id,
            name=clean_name(name, field="Store name", max_length=MAX_STORE_NAME),
            address=_clean_address(address),
            website_url=_clean_website(website_url),
            position=self._repo.max_store_position(poste_id) + POSITION_STEP,
        )
        self._repo.add_store(store)
        self._db.commit()
        return store


class UpdateStoreUseCase:
    """Rename a shop, correct its address or its website."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self, *, project_id: UUID, store_id: UUID, name: object, address: object, website_url: object
    ) -> ChiffrageStore:
        store = owned_store(self._repo, store_id, project_id)
        U = ChiffrageStore._UNSET
        updated = store.with_updates(
            name=(U if name is U else clean_name(str(name), field="Store name", max_length=MAX_STORE_NAME)),
            address=(U if address is U else _clean_address(address if address is None else str(address))),
            website_url=(
                U if website_url is U else _clean_website(website_url if website_url is None else str(website_url))
            ),
        )
        self._repo.save_store(updated)
        self._db.commit()
        return updated


class DeleteStoreUseCase:
    """Remove a shop from a poste."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, store_id: UUID) -> None:
        owned_store(self._repo, store_id, project_id)
        self._repo.delete_store(store_id)
        self._db.commit()
