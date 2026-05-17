"""ProjectDocument domain entity — metadata for a file attached to a project."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

# Maps (extension → kind tag) used for FE filtering and content classification.
# Public so the application layer can derive the kind from a filename without
# re-declaring the mapping. Use `kind_for_extension(ext)` for the safe lookup.
EXT_TO_KIND: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".xlsx": "spreadsheet",
    ".docx": "doc",
    ".dwg": "cad",
    ".txt": "text",
}


def kind_for_extension(extension: str) -> str:
    """Return the kind tag for a file extension, defaulting to 'other'.

    Args:
        extension: file extension with leading dot, case-insensitive (e.g. ".PDF").

    Returns:
        One of: "pdf" | "image" | "spreadsheet" | "doc" | "cad" | "text" | "other".
    """
    return EXT_TO_KIND.get(extension.lower(), "other")


@dataclass(frozen=True)
class ProjectDocument:
    """Domain entity representing a file attached to a project.

    The file content lives in object storage (MinIO/S3). This entity holds
    only the metadata + storage key needed to retrieve and manage it.
    Soft-deletion is tracked via deleted_at.
    """

    id: UUID
    project_id: UUID
    uploader_user_id: UUID
    filename: str  # original filename as uploaded by the user
    content_type: str  # MIME type
    size_bytes: int
    storage_key: str  # opaque key in the object store
    created_at: datetime
    deleted_at: Optional[datetime] = None

    def compute_kind(self) -> str:
        """Return a category tag for the document based on extension and MIME type.

        Tags: "pdf" | "image" | "spreadsheet" | "doc" | "cad" | "text" | "other"
        """
        ext = os.path.splitext(self.filename)[1].lower()
        if ext in EXT_TO_KIND:
            return EXT_TO_KIND[ext]
        # Fallback: check MIME prefix for images not covered by extension map.
        if self.content_type.startswith("image/"):
            return "image"
        if self.content_type == "application/pdf":
            return "pdf"
        if self.content_type == "text/plain":
            return "text"
        return "other"
