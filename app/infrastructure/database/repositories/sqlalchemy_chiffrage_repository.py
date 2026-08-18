"""SQLAlchemy adapter implementing ChiffrageRepositoryPort."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.entities.chiffrage_article import ChiffrageArticle
from app.domain.entities.chiffrage_poste import ChiffragePoste
from app.domain.entities.chiffrage_quote import ChiffrageQuote
from app.domain.entities.chiffrage_room import ChiffrageRoom
from app.domain.entities.chiffrage_store import ChiffrageStore
from app.domain.entities.chiffrage_unit import ChiffrageUnit
from app.infrastructure.database.models.chiffrage_article import ChiffrageArticleModel
from app.infrastructure.database.models.chiffrage_poste import ChiffragePosteModel
from app.infrastructure.database.models.chiffrage_quote import ChiffrageQuoteModel
from app.infrastructure.database.models.chiffrage_store import ChiffrageStoreModel
from app.infrastructure.database.models.chiffrage_room import ChiffrageRoomModel
from app.infrastructure.database.models.bibliotheque_product import BibliothequeProductModel
from app.infrastructure.database.models.chiffrage_unit import ChiffrageUnitModel


class SqlAlchemyChiffrageRepository:
    """CRUD + tree read for the chiffrage aggregate.

    SQLAlchemy 2.0 style throughout (select(), no legacy Query API).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Tree read
    # ------------------------------------------------------------------

    def get_tree(
        self, project_id: UUID
    ) -> tuple[list[ChiffragePoste], dict[UUID, list[ChiffrageArticle]], dict[UUID, list[ChiffrageQuote]]]:
        """Return the project's whole chiffrage in exactly three queries.

        One query per level, keyed in Python — never one query per article.
        """
        poste_rows = (
            self._session.execute(
                select(ChiffragePosteModel)
                .where(ChiffragePosteModel.project_id == project_id)
                .order_by(ChiffragePosteModel.position, ChiffragePosteModel.created_at)
            )
            .scalars()
            .all()
        )
        postes = [r.to_entity() for r in poste_rows]
        poste_ids = [p.id for p in postes]

        articles_by_poste: dict[UUID, list[ChiffrageArticle]] = {pid: [] for pid in poste_ids}
        quotes_by_article: dict[UUID, list[ChiffrageQuote]] = {}
        if not poste_ids:
            return postes, articles_by_poste, quotes_by_article

        article_rows = (
            self._session.execute(
                select(ChiffrageArticleModel)
                .where(ChiffrageArticleModel.poste_id.in_(poste_ids))
                .order_by(ChiffrageArticleModel.position, ChiffrageArticleModel.created_at)
            )
            .scalars()
            .all()
        )
        article_ids = []
        for row in article_rows:
            entity = row.to_entity()
            articles_by_poste.setdefault(entity.poste_id, []).append(entity)
            article_ids.append(entity.id)
            quotes_by_article[entity.id] = []

        if not article_ids:
            return postes, articles_by_poste, quotes_by_article

        quote_rows = (
            self._session.execute(
                select(ChiffrageQuoteModel)
                .where(ChiffrageQuoteModel.article_id.in_(article_ids))
                .order_by(ChiffrageQuoteModel.unit_price_ht, ChiffrageQuoteModel.created_at)
            )
            .scalars()
            .all()
        )
        for row in quote_rows:
            entity = row.to_entity()
            quotes_by_article.setdefault(entity.article_id, []).append(entity)

        return postes, articles_by_poste, quotes_by_article

    # ------------------------------------------------------------------
    # Poste
    # ------------------------------------------------------------------

    def find_poste(self, poste_id: UUID) -> Optional[ChiffragePoste]:
        orm = self._session.get(ChiffragePosteModel, poste_id)
        return orm.to_entity() if orm is not None else None

    def add_poste(self, poste: ChiffragePoste) -> None:
        self._session.add(ChiffragePosteModel.from_entity(poste))
        self._session.flush()

    def save_poste(self, poste: ChiffragePoste) -> None:
        orm = self._session.get(ChiffragePosteModel, poste.id)
        if orm is None:
            return
        orm.name = poste.name
        orm.note = poste.note
        orm.position = poste.position
        orm.updated_at = poste.updated_at
        self._session.flush()

    def delete_poste(self, poste_id: UUID) -> None:
        orm = self._session.get(ChiffragePosteModel, poste_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def max_poste_position(self, project_id: UUID) -> int:
        value = self._session.execute(
            select(func.max(ChiffragePosteModel.position)).where(ChiffragePosteModel.project_id == project_id)
        ).scalar()
        return int(value) if value is not None else 0

    # ------------------------------------------------------------------
    # Article
    # ------------------------------------------------------------------

    def find_article(self, article_id: UUID) -> Optional[ChiffrageArticle]:
        orm = self._session.get(ChiffrageArticleModel, article_id)
        return orm.to_entity() if orm is not None else None

    def find_article_for_update(self, article_id: UUID) -> Optional[ChiffrageArticle]:
        """Row-locked read; degrades to a plain SELECT on SQLite (tests)."""
        stmt = select(ChiffrageArticleModel).where(ChiffrageArticleModel.id == article_id).with_for_update()
        orm = self._session.execute(stmt).scalar_one_or_none()
        return orm.to_entity() if orm is not None else None

    def add_article(self, article: ChiffrageArticle) -> None:
        self._session.add(ChiffrageArticleModel.from_entity(article))
        self._session.flush()

    def save_article(self, article: ChiffrageArticle) -> None:
        orm = self._session.get(ChiffrageArticleModel, article.id)
        if orm is None:
            return
        orm.name = article.name
        orm.quantity = article.quantity
        orm.unit = article.unit
        orm.note = article.note
        orm.room_id = article.room_id
        orm.image_storage_key = article.image_storage_key
        orm.position = article.position
        orm.updated_at = article.updated_at
        self._session.flush()

    def delete_article(self, article_id: UUID) -> None:
        orm = self._session.get(ChiffrageArticleModel, article_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def max_article_position(self, poste_id: UUID) -> int:
        value = self._session.execute(
            select(func.max(ChiffrageArticleModel.position)).where(ChiffrageArticleModel.poste_id == poste_id)
        ).scalar()
        return int(value) if value is not None else 0

    # ------------------------------------------------------------------
    # Quote
    # ------------------------------------------------------------------

    def find_quote(self, quote_id: UUID) -> Optional[ChiffrageQuote]:
        orm = self._session.get(ChiffrageQuoteModel, quote_id)
        return orm.to_entity() if orm is not None else None

    def add_quote(self, quote: ChiffrageQuote) -> None:
        self._session.add(ChiffrageQuoteModel.from_entity(quote))
        self._session.flush()

    def save_quote(self, quote: ChiffrageQuote) -> None:
        orm = self._session.get(ChiffrageQuoteModel, quote.id)
        if orm is None:
            return
        orm.supplier_id = quote.supplier_id
        orm.supplier_name = quote.supplier_name
        orm.library_product_id = quote.library_product_id
        orm.unit_price_ht = quote.unit_price_ht
        orm.tva_rate = quote.tva_rate
        orm.product_url = quote.product_url
        orm.note = quote.note
        orm.is_selected = quote.is_selected
        orm.updated_at = quote.updated_at
        self._session.flush()

    def delete_quote(self, quote_id: UUID) -> None:
        orm = self._session.get(ChiffrageQuoteModel, quote_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def clear_selection(self, article_id: UUID) -> None:
        """Unselect every quote of the article in one statement.

        synchronize_session=False because the caller re-reads the affected rows
        through the repository rather than relying on identity-map state.
        """
        self._session.execute(
            update(ChiffrageQuoteModel)
            .where(ChiffrageQuoteModel.article_id == article_id, ChiffrageQuoteModel.is_selected.is_(True))
            .values(is_selected=False),
            execution_options={"synchronize_session": False},
        )
        self._session.flush()

    # ------------------------------------------------------------------
    # Rooms
    # ------------------------------------------------------------------

    def list_rooms(self, project_id: UUID) -> list[ChiffrageRoom]:
        rows = (
            self._session.execute(
                select(ChiffrageRoomModel)
                .where(ChiffrageRoomModel.project_id == project_id)
                .order_by(ChiffrageRoomModel.position, ChiffrageRoomModel.created_at)
            )
            .scalars()
            .all()
        )
        return [r.to_entity() for r in rows]

    def find_room(self, room_id: UUID) -> Optional[ChiffrageRoom]:
        orm = self._session.get(ChiffrageRoomModel, room_id)
        return orm.to_entity() if orm is not None else None

    def room_name_exists(self, project_id: UUID, name: str, exclude_id: Optional[UUID] = None) -> bool:
        stmt = select(ChiffrageRoomModel.id).where(
            ChiffrageRoomModel.project_id == project_id,
            ChiffrageRoomModel.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(ChiffrageRoomModel.id != exclude_id)
        return self._session.execute(stmt).first() is not None

    def add_room(self, room: ChiffrageRoom) -> None:
        self._session.add(ChiffrageRoomModel.from_entity(room))
        self._session.flush()

    def save_room(self, room: ChiffrageRoom) -> None:
        orm = self._session.get(ChiffrageRoomModel, room.id)
        if orm is None:
            return
        orm.name = room.name
        orm.position = room.position
        orm.updated_at = room.updated_at
        self._session.flush()

    def delete_room(self, room_id: UUID) -> None:
        orm = self._session.get(ChiffrageRoomModel, room_id)
        if orm is None:
            return
        # Detach the articles here rather than leaning on ON DELETE SET NULL:
        # SQLite does not enforce foreign keys by default, so the cascade would
        # fire on Postgres and silently not in the test suite. The FK stays as a
        # backstop for rows written outside this path.
        self._session.execute(
            update(ChiffrageArticleModel).where(ChiffrageArticleModel.room_id == room_id).values(room_id=None),
            execution_options={"synchronize_session": False},
        )
        self._session.delete(orm)
        self._session.flush()

    def max_room_position(self, project_id: UUID) -> int:
        value = self._session.execute(
            select(func.max(ChiffrageRoomModel.position)).where(ChiffrageRoomModel.project_id == project_id)
        ).scalar()
        return int(value) if value is not None else 0

    def count_articles_in_room(self, room_id: UUID) -> int:
        value = self._session.execute(
            select(func.count(ChiffrageArticleModel.id)).where(ChiffrageArticleModel.room_id == room_id)
        ).scalar()
        return int(value or 0)

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------

    def stores_for_postes(self, poste_ids: list[UUID]) -> dict[UUID, list[ChiffrageStore]]:
        """Return stores keyed by poste id in one query (never one per poste)."""
        result: dict[UUID, list[ChiffrageStore]] = {pid: [] for pid in poste_ids}
        if not poste_ids:
            return result
        rows = (
            self._session.execute(
                select(ChiffrageStoreModel)
                .where(ChiffrageStoreModel.poste_id.in_(poste_ids))
                .order_by(ChiffrageStoreModel.position, ChiffrageStoreModel.created_at)
            )
            .scalars()
            .all()
        )
        for row in rows:
            entity = row.to_entity()
            result.setdefault(entity.poste_id, []).append(entity)
        return result

    def find_store(self, store_id: UUID) -> Optional[ChiffrageStore]:
        orm = self._session.get(ChiffrageStoreModel, store_id)
        return orm.to_entity() if orm is not None else None

    def add_store(self, store: ChiffrageStore) -> None:
        self._session.add(ChiffrageStoreModel.from_entity(store))
        self._session.flush()

    def save_store(self, store: ChiffrageStore) -> None:
        orm = self._session.get(ChiffrageStoreModel, store.id)
        if orm is None:
            return
        orm.name = store.name
        orm.address = store.address
        orm.website_url = store.website_url
        orm.position = store.position
        orm.updated_at = store.updated_at
        self._session.flush()

    def delete_store(self, store_id: UUID) -> None:
        orm = self._session.get(ChiffrageStoreModel, store_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    def max_store_position(self, poste_id: UUID) -> int:
        value = self._session.execute(
            select(func.max(ChiffrageStoreModel.position)).where(ChiffrageStoreModel.poste_id == poste_id)
        ).scalar()
        return int(value) if value is not None else 0

    def project_id_for_store(self, store_id: UUID) -> Optional[UUID]:
        return self._session.execute(
            select(ChiffragePosteModel.project_id)
            .join(ChiffrageStoreModel, ChiffrageStoreModel.poste_id == ChiffragePosteModel.id)
            .where(ChiffrageStoreModel.id == store_id)
        ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Units
    # ------------------------------------------------------------------

    def list_units(self, project_id: UUID) -> list[ChiffrageUnit]:
        rows = (
            self._session.execute(
                select(ChiffrageUnitModel)
                .where(ChiffrageUnitModel.project_id == project_id)
                .order_by(ChiffrageUnitModel.symbol)
            )
            .scalars()
            .all()
        )
        return [r.to_entity() for r in rows]

    def find_unit(self, unit_id: UUID) -> Optional[ChiffrageUnit]:
        orm = self._session.get(ChiffrageUnitModel, unit_id)
        return orm.to_entity() if orm is not None else None

    def unit_exists(self, project_id: UUID, symbol: str) -> bool:
        found = self._session.execute(
            select(ChiffrageUnitModel.id).where(
                ChiffrageUnitModel.project_id == project_id,
                ChiffrageUnitModel.symbol == symbol,
            )
        ).first()
        return found is not None

    def add_unit(self, unit: ChiffrageUnit) -> None:
        self._session.add(ChiffrageUnitModel.from_entity(unit))
        self._session.flush()

    def delete_unit(self, unit_id: UUID) -> None:
        orm = self._session.get(ChiffrageUnitModel, unit_id)
        if orm is not None:
            self._session.delete(orm)
            self._session.flush()

    # ------------------------------------------------------------------
    # Ownership resolution — a nested id must never leak across projects
    # ------------------------------------------------------------------

    def project_id_for_poste(self, poste_id: UUID) -> Optional[UUID]:
        return self._session.execute(
            select(ChiffragePosteModel.project_id).where(ChiffragePosteModel.id == poste_id)
        ).scalar_one_or_none()

    def project_id_for_article(self, article_id: UUID) -> Optional[UUID]:
        return self._session.execute(
            select(ChiffragePosteModel.project_id)
            .join(ChiffrageArticleModel, ChiffrageArticleModel.poste_id == ChiffragePosteModel.id)
            .where(ChiffrageArticleModel.id == article_id)
        ).scalar_one_or_none()

    def library_products_with_image(self, product_ids: list[UUID]) -> set[UUID]:
        """One keyed query — never one lookup per article."""
        if not product_ids:
            return set()
        rows = (
            self._session.execute(
                select(BibliothequeProductModel.id).where(
                    BibliothequeProductModel.id.in_(product_ids),
                    BibliothequeProductModel.image_storage_key.is_not(None),
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    def project_id_for_quote(self, quote_id: UUID) -> Optional[UUID]:
        return self._session.execute(
            select(ChiffragePosteModel.project_id)
            .join(ChiffrageArticleModel, ChiffrageArticleModel.poste_id == ChiffragePosteModel.id)
            .join(ChiffrageQuoteModel, ChiffrageQuoteModel.article_id == ChiffrageArticleModel.id)
            .where(ChiffrageQuoteModel.id == quote_id)
        ).scalar_one_or_none()
