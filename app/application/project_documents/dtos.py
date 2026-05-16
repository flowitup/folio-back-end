"""DTOs for the project documents application layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from app.domain.project_document import ProjectDocument


@dataclass(frozen=True)
class ListFiltersDTO:
    """Filters and pagination options for listing project documents."""

    kinds: tuple[str, ...] = ()  # subset of (pdf, image, spreadsheet, doc, cad, text, other)
    uploader_id: Optional[UUID] = None
    sort: str = "created_at"  # one of (name, size, created_at, uploader)
    order: str = "desc"  # asc | desc
    page: int = 1
    per_page: int = 25


@dataclass(frozen=True)
class ListResultDTO:
    """Paginated result set for project document listings."""

    items: list[ProjectDocument]
    total: int
