"""SQLAlchemy adapter implementing IProjectPhotoRepository for project_photos."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.domain.project_photo import ProjectPhoto
from app.infrastructure.database.models.project_photo import ProjectPhotoRow


class SqlAlchemyProjectPhotoRepository:
    """Implements IProjectPhotoRepository against a SQLAlchemy session.

    The caller (use-case layer) owns the transaction boundary; this class only
    flushes to make PK/generated values visible within the same transaction.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # IProjectPhotoRepository
    # ------------------------------------------------------------------

    def save(self, photo: ProjectPhoto) -> ProjectPhoto:
        """Insert a new photo row and return the rehydrated entity.

        Flushes so DB-generated values are available in the returned entity.
        The caller is expected to commit via ITransactionalSession.
        """
        row = ProjectPhotoRow.from_domain(photo)
        self._session.add(row)
        self._session.flush()
        return row.to_domain()

    def find_by_id(self, photo_id: UUID) -> Optional[ProjectPhoto]:
        """Return the photo entity, or None if no row exists with that id.

        Intentionally does NOT filter on deleted_at IS NULL — callers (use-cases)
        decide whether a soft-deleted photo should be treated as not-found.
        """
        row = self._session.get(ProjectPhotoRow, photo_id)
        return row.to_domain() if row is not None else None

    def list_for_project(self, project_id: UUID, page: int, per_page: int) -> tuple[list[ProjectPhoto], int]:
        """Return (items, total) for active photos ordered captured_at DESC, created_at DESC.

        Excludes soft-deleted rows. Uses offset/limit pagination.
        """
        base_where = [
            ProjectPhotoRow.project_id == project_id,
            ProjectPhotoRow.deleted_at.is_(None),
        ]

        count_stmt = select(func.count()).select_from(ProjectPhotoRow).where(*base_where)
        total: int = self._session.execute(count_stmt).scalar_one()

        offset = (page - 1) * per_page
        data_stmt = (
            select(ProjectPhotoRow)
            .where(*base_where)
            .order_by(ProjectPhotoRow.captured_at.desc(), ProjectPhotoRow.created_at.desc())
            .limit(per_page)
            .offset(offset)
        )
        rows = self._session.execute(data_stmt).scalars().all()
        return [r.to_domain() for r in rows], total

    def update_metadata(self, photo_id: UUID, caption: Optional[str], captured_at: datetime) -> None:
        """UPDATE caption and captured_at for the given photo row.

        Uses a targeted UPDATE rather than a round-trip SELECT + mutate.
        No-op if the photo does not exist (use-case validates first).
        """
        stmt = (
            update(ProjectPhotoRow)
            .where(ProjectPhotoRow.id == photo_id)
            .values(caption=caption, captured_at=captured_at)
        )
        self._session.execute(stmt)
        self._session.flush()

    def soft_delete(self, photo_id: UUID, now: datetime) -> None:
        """Mark the photo as deleted by setting deleted_at = now.

        Guards on deleted_at IS NULL so concurrent soft-deletes on the same row
        are idempotent — only the first write sets the timestamp.
        """
        stmt = (
            update(ProjectPhotoRow)
            .where(
                ProjectPhotoRow.id == photo_id,
                ProjectPhotoRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        self._session.execute(stmt)
        self._session.flush()
