"""Phase write use-cases: create, update, delete, reorder.

A phase is a stage of works (giai đoạn) inside one poste: for the electricity,
conduits and boxes before the plasterboard, then the panel, then the switches.
Each poste orders its own stages; articles of that poste point into them.
"""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.exceptions import InvalidChiffrageInputError, PhaseAlreadyExistsError
from app.application.chiffrage.ordering import free_slot, renumber_around
from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import POSITION_STEP
from app.application.chiffrage.validation import MAX_PHASE_NAME, clean_name, owned_phase, owned_poste
from app.domain.entities.chiffrage_phase import ChiffragePhase


class ListPostePhasesUseCase:
    """A poste's stages in display order."""

    def __init__(self, repo: ChiffrageRepositoryPort) -> None:
        self._repo = repo

    def execute(self, *, project_id: UUID, poste_id: UUID) -> list[ChiffragePhase]:
        owned_poste(self._repo, poste_id, project_id)
        return self._repo.phases_in_poste(poste_id)


class CreatePhaseUseCase:
    """Append a stage to a poste."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, poste_id: UUID, name: str) -> ChiffragePhase:
        owned_poste(self._repo, poste_id, project_id)
        cleaned = clean_name(name, field="Phase name", max_length=MAX_PHASE_NAME)
        if self._repo.phase_name_exists(poste_id, cleaned):
            raise PhaseAlreadyExistsError(f"Phase '{cleaned}' already exists in this poste.")
        phase = ChiffragePhase.create(
            project_id=project_id,
            poste_id=poste_id,
            name=cleaned,
            position=self._repo.max_phase_position(poste_id) + POSITION_STEP,
        )
        self._repo.add_phase(phase)
        self._db.commit()
        return phase


class UpdatePhaseUseCase:
    """Rename a stage. Articles keep pointing at it — they hold its id, not its name."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, phase_id: UUID, name: str) -> ChiffragePhase:
        phase = owned_phase(self._repo, phase_id, project_id)
        cleaned = clean_name(name, field="Phase name", max_length=MAX_PHASE_NAME)
        if self._repo.phase_name_exists(phase.poste_id, cleaned, exclude_id=phase_id):
            raise PhaseAlreadyExistsError(f"Phase '{cleaned}' already exists in this poste.")
        updated = phase.with_updates(name=cleaned)
        self._repo.save_phase(updated)
        self._db.commit()
        return updated


class DeletePhaseUseCase:
    """Remove a stage.

    Articles that referenced it are not deleted — the FK is ON DELETE SET NULL,
    so they resurface without a stage rather than vanishing with it.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, phase_id: UUID) -> None:
        owned_phase(self._repo, phase_id, project_id)
        self._repo.delete_phase(phase_id)
        self._db.commit()


class ReorderPhaseUseCase:
    """Move a stage between two neighbours of the same poste."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        phase_id: UUID,
        before_id: UUID | None = None,
        after_id: UUID | None = None,
    ) -> ChiffragePhase:
        phase = owned_phase(self._repo, phase_id, project_id)
        before = owned_phase(self._repo, before_id, project_id) if before_id else None
        after = owned_phase(self._repo, after_id, project_id) if after_id else None
        for neighbour in (before, after):
            if neighbour is not None and neighbour.poste_id != phase.poste_id:
                raise InvalidChiffrageInputError("A phase can only be reordered within its own poste.")

        new_pos = free_slot(before, after, lambda: self._repo.max_phase_position(phase.poste_id))
        if new_pos is None:
            new_pos = renumber_around(
                self._repo.phases_in_poste(phase.poste_id),
                phase,
                before_id,
                after_id,
                lambda sibling, position: self._repo.save_phase(sibling.with_position(position)),
            )

        moved = phase.with_position(new_pos)
        self._repo.save_phase(moved)
        self._db.commit()
        return moved
