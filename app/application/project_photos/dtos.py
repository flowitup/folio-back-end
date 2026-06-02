"""DTOs for the project photos application layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from app.domain.project_photo import ProjectPhoto


@dataclass(frozen=True)
class UploadPhotoInput:
    """Input parameters for the upload use-case."""

    project_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    data: bytes
    uploader_user_id: UUID
    caption: Optional[str]
    captured_at: Optional[datetime]


@dataclass(frozen=True)
class PhotoListResult:
    """Paginated result set for project photo listings."""

    items: list[ProjectPhoto]
    total: int
