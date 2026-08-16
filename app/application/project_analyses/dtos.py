"""Data Transfer Objects for the project analyses application layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import IO, Optional
from uuid import UUID

from app.domain.entities.project_analysis import _UNSET, ProjectAnalysis, _Unset


@dataclass(frozen=True)
class CreateAnalysisInput:
    """Input for CreateProjectAnalysisUseCase — metadata + the raw upload stream."""

    project_id: UUID
    uploader_user_id: UUID
    title: str
    summary: Optional[str]
    source_url: Optional[str]
    tags: tuple[str, ...]
    filename: str
    content_type: str
    size_bytes: int
    # IO[bytes], not BinaryIO — matches werkzeug.datastructures.FileStorage.stream's
    # typeshed annotation, which is what the route actually passes in.
    fileobj: IO[bytes]


@dataclass(frozen=True)
class UpdateAnalysisInput:
    """Input for UpdateProjectAnalysisUseCase.

    Every optional field defaults to the ``_UNSET`` sentinel so a PATCH that
    only sets one field (e.g. summary) never clears the others. Route layer
    must build this from a Pydantic model via ``exclude_unset`` semantics —
    see ``app/api/v1/project_analyses/schemas.py``.
    """

    analysis_id: UUID
    title: Optional[str] = None
    summary: Optional[str] | _Unset = _UNSET
    source_url: Optional[str] | _Unset = _UNSET
    tags: tuple[str, ...] | _Unset = _UNSET


@dataclass(frozen=True)
class ListAnalysesInput:
    """Filters and pagination options for listing project analyses."""

    project_id: UUID
    q: Optional[str] = None
    tags: tuple[str, ...] = ()
    sort: str = "created_at"  # "created_at" | "title"
    order: str = "desc"  # "asc" | "desc"
    page: int = 1
    per_page: int = 25


@dataclass(frozen=True)
class AnalysisOutput:
    """Read model returned by analysis use-cases.

    Intentionally omits ``storage_key`` — it is an internal object-store
    path, never surfaced to API clients (mirrors ProjectDocument's
    ``_serialize`` in project_documents/routes.py, which derives a
    ``download_url`` instead of exposing the raw key).
    """

    id: UUID
    project_id: UUID
    uploader_user_id: UUID
    title: str
    summary: Optional[str]
    source_url: Optional[str]
    size_bytes: int
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, analysis: ProjectAnalysis) -> AnalysisOutput:
        """Build an AnalysisOutput from a ProjectAnalysis entity."""
        return cls(
            id=analysis.id,
            project_id=analysis.project_id,
            uploader_user_id=analysis.uploader_user_id,
            title=analysis.title,
            summary=analysis.summary,
            source_url=analysis.source_url,
            size_bytes=analysis.size_bytes,
            tags=analysis.tags,
            created_at=analysis.created_at,
            updated_at=analysis.updated_at,
        )


@dataclass(frozen=True)
class AnalysisPage:
    """Paginated result set for project analysis listings."""

    items: list[AnalysisOutput]
    total: int
    page: int
    per_page: int
