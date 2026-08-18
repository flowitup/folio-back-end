"""Article write use-cases: create, update, delete, reorder."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.application.chiffrage.exceptions import InvalidChiffrageInputError
from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import POSITION_STEP
from app.application.chiffrage.validation import (
    MAX_ARTICLE_NAME,
    clean_name,
    clean_optional_text,
    owned_article,
    owned_poste,
    owned_room,
    validate_quantity,
    validate_unit,
)
from app.domain.entities.chiffrage_article import ChiffrageArticle


class CreateArticleUseCase:
    """Append an article to a poste."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        poste_id: UUID,
        name: str,
        quantity: Decimal,
        unit: Optional[str] = None,
        note: Optional[str] = None,
        room_id: Optional[UUID] = None,
    ) -> ChiffrageArticle:
        owned_poste(self._repo, poste_id, project_id)
        # A room id from another project must not be attachable here.
        if room_id is not None:
            owned_room(self._repo, room_id, project_id)
        article = ChiffrageArticle.create(
            poste_id=poste_id,
            name=clean_name(name, field="Article name", max_length=MAX_ARTICLE_NAME),
            quantity=validate_quantity(quantity),
            unit=validate_unit(self._repo, project_id, unit),
            note=clean_optional_text(note),
            room_id=room_id,
            position=self._repo.max_article_position(poste_id) + POSITION_STEP,
        )
        self._repo.add_article(article)
        self._db.commit()
        return article


class UpdateArticleUseCase:
    """Edit an article's name, quantity, unit or note.

    Every optional field is threaded through with_updates; a field omitted here
    would silently drop on PATCH.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        article_id: UUID,
        name: object,
        quantity: object,
        unit: object,
        note: object,
        room_id: object,
    ) -> ChiffrageArticle:
        article = owned_article(self._repo, article_id, project_id)
        U = ChiffrageArticle._UNSET
        if room_id is not U and room_id is not None:
            owned_room(self._repo, UUID(str(room_id)), project_id)
        updated = article.with_updates(
            name=(U if name is U else clean_name(str(name), field="Article name", max_length=MAX_ARTICLE_NAME)),
            quantity=(U if quantity is U else validate_quantity(Decimal(str(quantity)))),
            unit=(U if unit is U else validate_unit(self._repo, project_id, None if unit is None else str(unit))),
            note=(U if note is U else clean_optional_text(note if note is None else str(note))),
            room_id=(U if room_id is U else (None if room_id is None else UUID(str(room_id)))),
        )
        self._repo.save_article(updated)
        self._db.commit()
        return updated


class DeleteArticleUseCase:
    """Delete an article; its quotes cascade at the DB level."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, article_id: UUID) -> None:
        owned_article(self._repo, article_id, project_id)
        self._repo.delete_article(article_id)
        self._db.commit()


class ReorderArticleUseCase:
    """Move an article within its own poste.

    Articles deliberately do not move across postes in v1: the drop contract
    stays a pure ordering change, so a mis-targeted drag cannot silently
    re-file a line under a different section of the budget.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        article_id: UUID,
        before_id: Optional[UUID] = None,
        after_id: Optional[UUID] = None,
    ) -> ChiffrageArticle:
        article = owned_article(self._repo, article_id, project_id)
        before = owned_article(self._repo, before_id, project_id) if before_id else None
        after = owned_article(self._repo, after_id, project_id) if after_id else None

        for neighbour in (before, after):
            if neighbour is not None and neighbour.poste_id != article.poste_id:
                raise InvalidChiffrageInputError("An article can only be reordered within its own poste.")

        if before and after:
            new_pos = (before.position + after.position) // 2
            if new_pos == before.position:
                new_pos = before.position + 1
        elif before:
            new_pos = before.position + POSITION_STEP
        elif after:
            new_pos = max(0, after.position - POSITION_STEP)
        else:
            new_pos = self._repo.max_article_position(article.poste_id) + POSITION_STEP

        moved = article.with_position(new_pos)
        self._repo.save_article(moved)
        self._db.commit()
        return moved
