"""In-memory document storage adapter — for tests and local development."""

from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from app.application.project_documents.ports import IDocumentStorage


class InMemoryDocumentStorage(IDocumentStorage):
    """Stores files in a plain dict. Satisfies IDocumentStorage without S3."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        data = fileobj.read()
        self._store[key] = (data, content_type)

    def get_stream(self, key: str) -> tuple[BinaryIO, int]:
        if key not in self._store:
            raise FileNotFoundError(key)
        data, _ = self._store[key]
        return BytesIO(data), len(data)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    # ------------------------------------------------------------------
    # Presigned URL stubs (not available in-memory)
    # ------------------------------------------------------------------

    @property
    def presigned_uploads_enabled(self) -> bool:
        return False

    def generate_presigned_put_url(self, key: str, content_type: str, expires_in: int = 600) -> str:
        raise RuntimeError("Presigned PUT not available in-memory")

    def generate_presigned_get_url(self, key: str, expires_in: int = 3600) -> str:
        raise RuntimeError("Presigned GET not available in-memory")

    def head_object(self, key: str) -> dict | None:
        if key not in self._store:
            return None
        data, ct = self._store[key]
        return {"ContentLength": len(data), "ContentType": ct}

    # ------------------------------------------------------------------
    # Test convenience helpers
    # ------------------------------------------------------------------

    def has(self, key: str) -> bool:
        return key in self._store

    def clear(self) -> None:
        self._store.clear()
