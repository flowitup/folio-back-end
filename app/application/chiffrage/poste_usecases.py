"""Poste write use-cases: create, update, delete, reorder."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import POSITION_STEP
from app.application.chiffrage.validation import (
    MAX_POSTE_NAME,
    clean_name,
    clean_optional_text,
    owned_poste,
)
from app.domain.entities.chiffrage_poste import ChiffragePoste


class CreatePosteUseCase:
    """Append a new poste at the end of the project's list."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, name: str, note: Optional[str] = None) -> ChiffragePoste:
        poste = ChiffragePoste.create(
            project_id=project_id,
            name=clean_name(name, field="Poste name", max_length=MAX_POSTE_NAME),
            note=clean_optional_text(note),
            position=self._repo.max_poste_position(project_id) + POSITION_STEP,
        )
        self._repo.add_poste(poste)
        self._db.commit()
        return poste


class UpdatePosteUseCase:
    """Rename a poste or change its note."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, poste_id: UUID, name: object, note: object) -> ChiffragePoste:
        """Fields left as the entity's _UNSET sentinel keep their current value."""
        poste = owned_poste(self._repo, poste_id, project_id)
        U = ChiffragePoste._UNSET
        updated = poste.with_updates(
            name=(U if name is U else clean_name(str(name), field="Poste name", max_length=MAX_POSTE_NAME)),
            note=(U if note is U else clean_optional_text(note if note is None else str(note))),
        )
        self._repo.save_poste(updated)
        self._db.commit()
        return updated


class DeletePosteUseCase:
    """Delete a poste; its articles and quotes cascade at the DB level."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, poste_id: UUID) -> None:
        owned_poste(self._repo, poste_id, project_id)
        self._repo.delete_poste(poste_id)
        self._db.commit()


class ReorderPosteUseCase:
    """Move a poste between two neighbours.

    The drop target is expressed as the poste above (`before_id`) and below
    (`after_id`) the gap, mirroring the task board. Sending neighbours rather
    than a raw index keeps the result deterministic when the client's view is
    momentarily stale, and avoids renumbering the whole list on every drag.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        poste_id: UUID,
        before_id: Optional[UUID] = None,
        after_id: Optional[UUID] = None,
    ) -> ChiffragePoste:
        poste = owned_poste(self._repo, poste_id, project_id)
        before = owned_poste(self._repo, before_id, project_id) if before_id else None
        after = owned_poste(self._repo, after_id, project_id) if after_id else None

        if before and after:
            new_pos = (before.position + after.position) // 2
            if new_pos == before.position:
                # Integer gap exhausted between these two neighbours.
                new_pos = before.position + 1
        elif before:
            new_pos = before.position + POSITION_STEP
        elif after:
            new_pos = max(0, after.position - POSITION_STEP)
        else:
            new_pos = self._repo.max_poste_position(project_id) + POSITION_STEP

        moved = poste.with_position(new_pos)
        self._repo.save_poste(moved)
        self._db.commit()
        return moved
