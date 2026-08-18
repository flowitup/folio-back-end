"""Room write use-cases: create, update, delete, reorder.

Rooms are declared once per project and reused by every poste — the rooms of a
chantier do not change from one trade to the next.
"""

from __future__ import annotations

from uuid import UUID

from app.application.chiffrage.exceptions import RoomAlreadyExistsError
from app.application.chiffrage.ports import ChiffrageRepositoryPort, TransactionalSessionPort
from app.application.chiffrage.units import POSITION_STEP
from app.application.chiffrage.validation import MAX_ROOM_NAME, clean_name, owned_room
from app.domain.entities.chiffrage_room import ChiffrageRoom


class CreateRoomUseCase:
    """Append a room to the project's list."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, name: str) -> ChiffrageRoom:
        cleaned = clean_name(name, field="Room name", max_length=MAX_ROOM_NAME)
        if self._repo.room_name_exists(project_id, cleaned):
            raise RoomAlreadyExistsError(f"Room '{cleaned}' already exists in this project.")
        room = ChiffrageRoom.create(
            project_id=project_id,
            name=cleaned,
            position=self._repo.max_room_position(project_id) + POSITION_STEP,
        )
        self._repo.add_room(room)
        self._db.commit()
        return room


class UpdateRoomUseCase:
    """Rename a room. Articles keep pointing at it — they hold its id, not its name."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, room_id: UUID, name: str) -> ChiffrageRoom:
        room = owned_room(self._repo, room_id, project_id)
        cleaned = clean_name(name, field="Room name", max_length=MAX_ROOM_NAME)
        if self._repo.room_name_exists(project_id, cleaned, exclude_id=room_id):
            raise RoomAlreadyExistsError(f"Room '{cleaned}' already exists in this project.")
        updated = room.with_updates(name=cleaned)
        self._repo.save_room(updated)
        self._db.commit()
        return updated


class DeleteRoomUseCase:
    """Remove a room.

    Articles that referenced it are not deleted — the FK is ON DELETE SET NULL,
    so they resurface as unassigned rather than vanishing with the room.
    """

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(self, *, project_id: UUID, room_id: UUID) -> None:
        owned_room(self._repo, room_id, project_id)
        self._repo.delete_room(room_id)
        self._db.commit()


class ReorderRoomUseCase:
    """Move a room between two neighbours."""

    def __init__(self, repo: ChiffrageRepositoryPort, db_session: TransactionalSessionPort) -> None:
        self._repo = repo
        self._db = db_session

    def execute(
        self,
        *,
        project_id: UUID,
        room_id: UUID,
        before_id: UUID | None = None,
        after_id: UUID | None = None,
    ) -> ChiffrageRoom:
        room = owned_room(self._repo, room_id, project_id)
        before = owned_room(self._repo, before_id, project_id) if before_id else None
        after = owned_room(self._repo, after_id, project_id) if after_id else None

        if before is not None and after is not None:
            new_pos = (before.position + after.position) // 2
        elif before is not None:
            new_pos = before.position + POSITION_STEP
        elif after is not None:
            new_pos = max(0, after.position - POSITION_STEP)
        else:
            new_pos = self._repo.max_room_position(project_id) + POSITION_STEP

        moved = room.with_position(new_pos)
        self._repo.save_room(moved)
        self._db.commit()
        return moved
