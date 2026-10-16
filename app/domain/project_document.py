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


# The MIME type a document of each extension is stored and served with. The
# client's declared type is not trusted (browsers send octet-stream for
# .docx/.webp, and anything can be declared): the validated extension decides.
# DWG has no stable MIME type and is absent on purpose.
EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


def canonical_content_type(filename: str, fallback: str) -> str:
    """The MIME type for a document named ``filename``.

    Derived from the extension when it is a known one; otherwise ``fallback``
    (the stored or declared type) is returned unchanged.
    """
    return EXT_TO_MIME.get(os.path.splitext(filename)[1].lower(), fallback)


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
    tags: tuple[str, ...] = ()

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

    @property
    def effective_content_type(self) -> str:
        """The content type to serve: canonical for the extension, else the stored one.

        Rows stored before uploads normalised the type may carry the client's
        octet-stream or a type that contradicts the extension.
        """
        return canonical_content_type(self.filename, self.content_type)
