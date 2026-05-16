"""SQLAlchemy adapter implementing IProjectDocumentRepository for project_documents."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.application.project_documents.dtos import ListFiltersDTO, ListResultDTO
from app.domain.project_document import ProjectDocument
from app.infrastructure.database.models.project_document import ProjectDocumentModel

# ---------------------------------------------------------------------------
# Kind → file-extension mapping used for SQL-side filtering.
#
# The "other" kind is handled specially: it matches files whose extension does
# NOT appear in any of the known-extension lists (negation clause).
# ---------------------------------------------------------------------------
_KIND_EXTENSIONS: dict[str, list[str]] = {
    "pdf": [".pdf"],
    "image": [".png", ".jpg", ".jpeg", ".webp"],
    "spreadsheet": [".xlsx"],
    "doc": [".docx"],
    "cad": [".dwg"],
    "text": [".txt"],
    "other": [],  # no positive extension set; matched by negation of all known exts
}

_ALL_KNOWN_EXTENSIONS: list[str] = [ext for kind, exts in _KIND_EXTENSIONS.items() if kind != "other" for ext in exts]


class SqlAlchemyProjectDocumentRepository:
    """Implements IProjectDocumentRepository against a SQLAlchemy session.

    Constructor accepts a Session directly, matching the pattern used by
    SqlAlchemyNoteRepository and SqlAlchemyBillingDocumentRepository in this
    codebase. The caller (use-case layer) owns the transaction boundary.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # IProjectDocumentRepository
    # ------------------------------------------------------------------

    def save(self, doc: ProjectDocument) -> ProjectDocument:
        """Insert a new document record and return the rehydrated entity.

        Flushes (but does not commit) so that the DB-generated PK is available
        in the returned entity. The caller is expected to commit via the
        ITransactionalSession port.
        """
        model = ProjectDocumentModel.from_domain(doc)
        self._session.add(model)
        self._session.flush()
        return model.to_domain()

    def find_by_id(self, doc_id: UUID) -> Optional[ProjectDocument]:
        """Return the document entity, or None if no row exists with that id.

        Intentionally does NOT filter on deleted_at IS NULL — callers (use-cases)
        decide whether a soft-deleted document should be treated as not-found.
        This keeps the repository neutral so a future "trash" view can reuse it.
        """
        model = self._session.get(ProjectDocumentModel, doc_id)
        return model.to_domain() if model is not None else None

    def list_for_project(
        self,
        project_id: UUID,
        filters: ListFiltersDTO,
    ) -> ListResultDTO:
        """Return a paginated, filtered list of active documents for a project.

        Kind filtering uses case-insensitive LIKE on filename extension, which
        works on both PostgreSQL and SQLite (used in tests). This keeps
        pagination counts accurate — no post-fetch Python-side trimming.

        For the "other" kind: files whose lowercase extension does NOT match
        any known extension are selected via a negation clause.
        """
        base_where = [
            ProjectDocumentModel.project_id == project_id,
            ProjectDocumentModel.deleted_at.is_(None),
        ]

        # ------------------------------------------------------------------
        # Kind filter (SQL-side, cross-DB compatible via LIKE patterns)
        # ------------------------------------------------------------------
        if filters.kinds:
            kind_clauses = []
            include_other = "other" in filters.kinds

            for kind in filters.kinds:
                if kind == "other":
                    continue  # handled separately below
                exts = _KIND_EXTENSIONS.get(kind, [])
                for ext in exts:
                    # lower(filename) LIKE '%.pdf' — SQLite and PG both support this
                    kind_clauses.append(func.lower(ProjectDocumentModel.filename).like(f"%{ext}"))

            if include_other:
                # "other" = extension is not in any known list
                known_not_clauses = [
                    func.lower(ProjectDocumentModel.filename).like(f"%{ext}") for ext in _ALL_KNOWN_EXTENSIONS
                ]
                if known_not_clauses:
                    from sqlalchemy import not_

                    other_clause = not_(or_(*known_not_clauses))
                else:  # pragma: no cover
                    # NOTE: _ALL_KNOWN_EXTENSIONS is hardcoded non-empty; this branch
                    # is a defensive guard for future schema changes and cannot be
                    # reached with the current extension registry.
                    from sqlalchemy import true

                    other_clause = true()
                kind_clauses.append(other_clause)

            if kind_clauses:
                base_where.append(or_(*kind_clauses))

        # ------------------------------------------------------------------
        # Uploader filter
        # ------------------------------------------------------------------
        if filters.uploader_id is not None:
            base_where.append(ProjectDocumentModel.uploader_user_id == filters.uploader_id)

        # ------------------------------------------------------------------
        # Sorting  (each sort key has an `id` tiebreaker for stable paging)
        # ------------------------------------------------------------------
        asc_order = filters.order == "asc"
        order_fn = lambda col: col.asc() if asc_order else col.desc()  # noqa: E731

        sort_key = filters.sort
        if sort_key == "name":
            order_by = [
                order_fn(func.lower(ProjectDocumentModel.filename)),
                order_fn(ProjectDocumentModel.id),
            ]
        elif sort_key == "size":
            order_by = [
                order_fn(ProjectDocumentModel.size_bytes),
                order_fn(ProjectDocumentModel.id),
            ]
        elif sort_key == "uploader":
            # v1: sort by uploader_user_id cast to text — acceptable simple sort
            order_by = [
                order_fn(ProjectDocumentModel.uploader_user_id),
                order_fn(ProjectDocumentModel.id),
            ]
        else:
            # "created_at" (default) and any unknown value fall back to created_at
            order_by = [
                order_fn(ProjectDocumentModel.created_at),
                order_fn(ProjectDocumentModel.id),
            ]

        # ------------------------------------------------------------------
        # Count query (same WHERE, no ORDER BY / LIMIT)
        # ------------------------------------------------------------------
        count_stmt = select(func.count()).select_from(ProjectDocumentModel).where(*base_where)
        total: int = self._session.execute(count_stmt).scalar_one()

        # ------------------------------------------------------------------
        # Data query with pagination
        # ------------------------------------------------------------------
        offset = (filters.page - 1) * filters.per_page
        data_stmt = (
            select(ProjectDocumentModel).where(*base_where).order_by(*order_by).limit(filters.per_page).offset(offset)
        )
        models = self._session.execute(data_stmt).scalars().all()

        return ListResultDTO(
            items=[m.to_domain() for m in models],
            total=total,
        )

    def soft_delete(self, doc_id: UUID, now: datetime) -> None:
        """Mark the document as deleted by setting deleted_at = now.

        Uses a targeted UPDATE rather than a round-trip SELECT + mutate so that
        concurrent soft-deletes on the same row are handled cleanly (only the
        first one wins due to the `deleted_at IS NULL` guard). No-op if the
        document is already deleted or does not exist.
        """
        stmt = (
            update(ProjectDocumentModel)
            .where(
                ProjectDocumentModel.id == doc_id,
                ProjectDocumentModel.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        self._session.execute(stmt)
        self._session.flush()
