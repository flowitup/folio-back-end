"""Unit tests for ProjectDocument.compute_kind() and InMemoryDocumentStorage.

These tests close coverage gaps on:
  - app.domain.project_document (MIME-based fallback paths in compute_kind)
  - app.infrastructure.adapters.in_memory_document_storage (get_stream miss, delete, has, clear)
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4

import pytest

from app.domain.project_document import ProjectDocument
from app.infrastructure.adapters.in_memory_document_storage import InMemoryDocumentStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(filename: str = "file.pdf", content_type: str = "application/pdf") -> ProjectDocument:
    return ProjectDocument(
        id=uuid4(),
        project_id=uuid4(),
        uploader_user_id=uuid4(),
        filename=filename,
        content_type=content_type,
        size_bytes=100,
        storage_key="project-documents/x/y/file",
        created_at=datetime.now(timezone.utc),
    )


# ===========================================================================
# ProjectDocument.compute_kind — MIME fallback branches
# ===========================================================================


class TestComputeKind:
    # Extension-based (already covered implicitly, but explicit here for clarity)
    def test_pdf_extension(self):
        assert _make_doc("report.pdf", "application/pdf").compute_kind() == "pdf"

    def test_png_extension(self):
        assert _make_doc("photo.png", "image/png").compute_kind() == "image"

    def test_jpg_extension(self):
        assert _make_doc("photo.jpg", "image/jpeg").compute_kind() == "image"

    def test_jpeg_extension(self):
        assert _make_doc("photo.jpeg", "image/jpeg").compute_kind() == "image"

    def test_webp_extension(self):
        assert _make_doc("img.webp", "image/webp").compute_kind() == "image"

    def test_xlsx_extension(self):
        ct = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert _make_doc("sheet.xlsx", ct).compute_kind() == "spreadsheet"

    def test_docx_extension(self):
        ct = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert _make_doc("doc.docx", ct).compute_kind() == "doc"

    def test_dwg_extension(self):
        assert _make_doc("plan.dwg", "image/vnd.dwg").compute_kind() == "cad"

    def test_txt_extension(self):
        assert _make_doc("notes.txt", "text/plain").compute_kind() == "text"

    # MIME fallback paths — extension not in _EXT_TO_KIND map
    def test_mime_image_fallback_for_unknown_image_ext(self):
        """Unknown extension with image/* MIME → 'image'."""
        doc = _make_doc("photo.tiff", "image/tiff")
        assert doc.compute_kind() == "image"

    def test_mime_image_gif_fallback(self):
        """GIF is not in extension map but image/gif MIME triggers image kind."""
        doc = _make_doc("anim.gif", "image/gif")
        assert doc.compute_kind() == "image"

    def test_mime_pdf_fallback_for_unknown_ext(self):
        """Unknown extension with application/pdf MIME → 'pdf'."""
        doc = _make_doc("mystery.xyz", "application/pdf")
        assert doc.compute_kind() == "pdf"

    def test_mime_text_fallback_for_unknown_ext(self):
        """Unknown extension with text/plain MIME → 'text'."""
        doc = _make_doc("config.cfg", "text/plain")
        assert doc.compute_kind() == "text"

    def test_other_for_unknown_ext_and_mime(self):
        """Unknown extension with unknown MIME → 'other'."""
        doc = _make_doc("mystery.xyz", "application/octet-stream")
        assert doc.compute_kind() == "other"


# ===========================================================================
# InMemoryDocumentStorage
# ===========================================================================


class TestInMemoryDocumentStorage:
    def test_put_and_get_stream(self):
        storage = InMemoryDocumentStorage()
        data = b"file content here"
        storage.put("my/key", BytesIO(data), "application/pdf")

        stream, length = storage.get_stream("my/key")
        assert stream.read() == data
        assert length == len(data)

    def test_get_stream_missing_key_raises(self):
        storage = InMemoryDocumentStorage()
        with pytest.raises(FileNotFoundError):
            storage.get_stream("nonexistent/key")

    def test_delete_removes_key(self):
        storage = InMemoryDocumentStorage()
        storage.put("key/to/delete", BytesIO(b"data"), "text/plain")

        storage.delete("key/to/delete")

        assert not storage.has("key/to/delete")

    def test_delete_nonexistent_key_is_noop(self):
        """Deleting a non-existent key must not raise."""
        storage = InMemoryDocumentStorage()
        storage.delete("key/that/does/not/exist")  # should not raise

    def test_has_returns_true_for_existing_key(self):
        storage = InMemoryDocumentStorage()
        storage.put("exists", BytesIO(b"x"), "text/plain")
        assert storage.has("exists") is True

    def test_has_returns_false_for_missing_key(self):
        storage = InMemoryDocumentStorage()
        assert storage.has("missing") is False

    def test_clear_removes_all_keys(self):
        storage = InMemoryDocumentStorage()
        storage.put("a", BytesIO(b"1"), "text/plain")
        storage.put("b", BytesIO(b"2"), "text/plain")

        storage.clear()

        assert not storage.has("a")
        assert not storage.has("b")

    def test_put_overwrites_existing_key(self):
        storage = InMemoryDocumentStorage()
        storage.put("key", BytesIO(b"old"), "text/plain")
        storage.put("key", BytesIO(b"new"), "application/pdf")

        stream, length = storage.get_stream("key")
        assert stream.read() == b"new"
        assert length == 3
