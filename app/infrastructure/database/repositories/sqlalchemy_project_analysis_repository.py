"""SQLAlchemy adapter implementing ProjectAnalysisRepositoryPort."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.entities.project_analysis import ProjectAnalysis
from app.infrastructure.database.models.project_analysis import ProjectAnalysisModel, ProjectAnalysisTagRow


class SqlAlchemyProjectAnalysisRepository:
    """Implements ProjectAnalysisRepositoryPort against a SQLAlchemy session.

    Constructor accepts a Session directly, matching the pattern used by
    SqlAlchemyNoteRepository and SqlAlchemyProjectDocumentRepository in this
    codebase. The caller (use-case layer) owns the transaction boundary.

    Every read filters ``deleted_at IS NULL`` so soft-deleted rows never
    resurface in list/tag queries — ``find_by_id`` / ``find_by_id_for_update``
    are the deliberate exception: they return the raw row (deleted or not) so
    callers can distinguish "not found" from "already deleted" when needed.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # ProjectAnalysisRepositoryPort
    # ------------------------------------------------------------------

    def find_by_id(self, analysis_id: UUID) -> Optional[ProjectAnalysis]:
        """Return the analysis entity, or None if no row exists with that id."""
        model = self._session.get(ProjectAnalysisModel, analysis_id)
        return model.to_domain() if model is not None else None

    def find_by_id_for_update(self, analysis_id: UUID) -> Optional[ProjectAnalysis]:
        """Return the analysis with a SELECT FOR UPDATE row lock, or None.

        Serializes concurrent mutations (e.g. delete racing an update).
        Falls back to a plain SELECT on dialects without FOR UPDATE support
        (SQLite in tests — no concurrent transactions there anyway).
        """
        stmt = select(ProjectAnalysisModel).where(ProjectAnalysisModel.id == analysis_id).with_for_update()
        model = self._session.execute(stmt).scalar_one_or_none()
        return model.to_domain() if model is not None else None

    def list_by_project(
        self,
        project_id: UUID,
        *,
        q: Optional[str] = None,
        tags: tuple[str, ...] = (),
        sort: str = "created_at",
        order: str = "desc",
        page: int = 1,
        per_page: int = 25,
    ) -> tuple[list[ProjectAnalysis], int]:
        """Return a paginated, filtered list of active analyses for a project."""
        base_where = [
            ProjectAnalysisModel.project_id == project_id,
            ProjectAnalysisModel.deleted_at.is_(None),
        ]

        # ------------------------------------------------------------------
        # Free-text filter — case-insensitive substring match on title + summary
        # ------------------------------------------------------------------
        if q:
            pattern = f"%{q.lower()}%"
            base_where.append(
                or_(
                    func.lower(ProjectAnalysisModel.title).like(pattern),
                    func.lower(ProjectAnalysisModel.summary).like(pattern),
                )
            )

        # ------------------------------------------------------------------
        # Tag filter — analysis must carry ALL requested tags (AND semantics)
        # ------------------------------------------------------------------
        for tag_value in tags:
            tag_exists = (
                select(ProjectAnalysisTagRow.analysis_id)
                .where(
                    ProjectAnalysisTagRow.analysis_id == ProjectAnalysisModel.id,
                    ProjectAnalysisTagRow.tag == tag_value,
                )
                .correlate(ProjectAnalysisModel)
                .exists()
            )
            base_where.append(tag_exists)

        # ------------------------------------------------------------------
        # Sorting (each sort key has an `id` tiebreaker for stable paging)
        # ------------------------------------------------------------------
        asc_order = order == "asc"

        def order_fn(col: Any) -> Any:
            return col.asc() if asc_order else col.desc()

        if sort == "title":
            order_by = [order_fn(func.lower(ProjectAnalysisModel.title)), order_fn(ProjectAnalysisModel.id)]
        else:
            # "created_at" (default) and any unknown value fall back to created_at
            order_by = [order_fn(ProjectAnalysisModel.created_at), order_fn(ProjectAnalysisModel.id)]

        # ------------------------------------------------------------------
        # Count query (same WHERE, no ORDER BY / LIMIT)
        # ------------------------------------------------------------------
        count_stmt = select(func.count()).select_from(ProjectAnalysisModel).where(*base_where)
        total: int = self._session.execute(count_stmt).scalar_one()

        # ------------------------------------------------------------------
        # Data query with pagination
        # ------------------------------------------------------------------
        offset = (page - 1) * per_page
        data_stmt = select(ProjectAnalysisModel).where(*base_where).order_by(*order_by).limit(per_page).offset(offset)
        models = self._session.execute(data_stmt).scalars().all()

        return [m.to_domain() for m in models], total

    def list_tags_for_project(self, project_id: UUID) -> list[str]:
        """Return all distinct tags used by active analyses in a project."""
        stmt = (
            select(ProjectAnalysisTagRow.tag)
            .join(ProjectAnalysisModel, ProjectAnalysisTagRow.analysis_id == ProjectAnalysisModel.id)
            .where(
                ProjectAnalysisModel.project_id == project_id,
                ProjectAnalysisModel.deleted_at.is_(None),
            )
            .distinct()
            .order_by(ProjectAnalysisTagRow.tag)
        )
        return list(self._session.execute(stmt).scalars().all())

    def add(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        """Insert a new analysis record and return the rehydrated entity.

        Flushes (but does not commit) so the caller's TransactionalSessionPort
        controls the commit boundary.
        """
        model = ProjectAnalysisModel.from_domain(analysis)
        self._session.add(model)
        self._session.flush()
        return model.to_domain()

    def save(self, analysis: ProjectAnalysis) -> ProjectAnalysis:
        """Persist metadata + a wholesale tag replacement for an existing analysis.

        Tags are replaced through the ``_tags`` relationship's
        ``cascade="all, delete-orphan"`` — reassigning the collection lets
        the ORM unit-of-work diff old vs. new and issue the right
        DELETE/INSERT set with the identity map kept in sync.

        NOTE: a raw bulk ``DELETE ... synchronize_session=False`` followed by
        ``session.add()`` (the pattern used by
        SqlAlchemyProjectDocumentRepository.set_tags) was tried here first and
        rejected — the call path always goes through
        ``find_by_id_for_update``, whose ``lazy="selectin"`` eager-loads the
        old tag rows into the identity map earlier in the same request. A
        synchronize_session=False bulk delete does not clear that map, so
        re-adding a row with the same composite PK collides with the
        still-tracked (soon-to-be-deleted) persistent instance — verified
        empirically via a real save() call: SQLAlchemy raised/warned on the
        identity conflict. The relationship-cascade approach below has no
        such gap.

        Raises:
            ValueError: no row exists with ``analysis.id`` — the use-case
                layer is expected to have already loaded (and thus guaranteed
                the existence of) the row via find_by_id_for_update.
        """
        model = self._session.get(ProjectAnalysisModel, analysis.id)
        if model is None:
            raise ValueError(f"Analysis {analysis.id} not found — cannot save.")

        model.title = analysis.title
        model.summary = analysis.summary
        model.source_url = analysis.source_url
        model.updated_at = analysis.updated_at
        model.deleted_at = analysis.deleted_at
        model._tags = [ProjectAnalysisTagRow(analysis_id=analysis.id, tag=t) for t in analysis.tags]

        self._session.flush()
        return analysis
