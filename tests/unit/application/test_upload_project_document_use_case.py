"""Unit tests for UploadProjectDocumentUseCase."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.project_documents.exceptions import (
    DocumentFileTooLargeError,
    EmptyFileError,
    UnsupportedDocumentTypeError,
)
from app.application.project_documents.ports import IDocumentStorage, IProjectDocumentRepository
from app.application.project_documents.upload_project_document import (
    MAX_SIZE_BYTES,
    UploadProjectDocumentUseCase,
)
from app.domain.project_document import ProjectDocument
from app.infrastructure.adapters.werkzeug_filename_sanitizer import WerkzeugFilenameSanitizer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo() -> MagicMock:
    repo = MagicMock(spec=IProjectDocumentRepository)
    repo.save.side_effect = lambda doc: doc
    return repo


def _make_storage() -> MagicMock:
    return MagicMock(spec=IDocumentStorage)


def _make_session() -> MagicMock:
    session = MagicMock()
    session.commit = MagicMock()
    return session


def _make_use_case(repo=None, storage=None, session=None):
    repo = repo or _make_repo()
    storage = storage or _make_storage()
    session = session or _make_session()
    return (
        UploadProjectDocumentUseCase(
            repo=repo,
            storage=storage,
            db_session=session,
            filename_sanitizer=WerkzeugFilenameSanitizer(),
        ),
        repo,
        storage,
        session,
    )


def _fileobj(content: bytes = b"hello") -> BytesIO:
    return BytesIO(content)


class TestUploadHappyPath:
    def test_returns_saved_doc(self):
        uc, repo, storage, session = _make_use_case()
        project_id = uuid4()
        uploader_id = uuid4()

        result = uc.execute(
            project_id=project_id,
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            fileobj=_fileobj(b"x" * 1024),
            uploader_user_id=uploader_id,
        )

        assert isinstance(result, ProjectDocument)
        assert result.project_id == project_id
        assert result.uploader_user_id == uploader_id
        assert result.filename == "report.pdf"

    def test_storage_put_called_once(self):
        uc, repo, storage, session = _make_use_case()

        uc.execute(
            project_id=uuid4(),
            filename="photo.jpg",
            content_type="image/jpeg",
            size_bytes=500,
            fileobj=_fileobj(b"x" * 500),
            uploader_user_id=uuid4(),
        )

        storage.put.assert_called_once()

    def test_db_commit_called_once(self):
        uc, repo, storage, session = _make_use_case()

        uc.execute(
            project_id=uuid4(),
            filename="doc.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=2048,
            fileobj=_fileobj(b"y" * 2048),
            uploader_user_id=uuid4(),
        )

        session.commit.assert_called_once()

    def test_repo_save_called_with_domain_entity(self):
        uc, repo, storage, session = _make_use_case()

        uc.execute(
            project_id=uuid4(),
            filename="sheet.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=100,
            fileobj=_fileobj(b"x" * 100),
            uploader_user_id=uuid4(),
        )

        repo.save.assert_called_once()
        saved_arg = repo.save.call_args[0][0]
        assert isinstance(saved_arg, ProjectDocument)


class TestUploadSizeValidation:
    def test_empty_file_raises_empty_file_error(self):
        """Zero-byte file raises EmptyFileError (→ 400), not FileTooLargeError (→ 413)."""
        uc, _, storage, _ = _make_use_case()

        with pytest.raises(EmptyFileError):
            uc.execute(
                project_id=uuid4(),
                filename="empty.pdf",
                content_type="application/pdf",
                size_bytes=0,
                fileobj=_fileobj(b""),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()

    def test_negative_size_raises_empty_file_error(self):
        """Negative size also raises EmptyFileError (→ 400)."""
        uc, _, storage, _ = _make_use_case()

        with pytest.raises(EmptyFileError):
            uc.execute(
                project_id=uuid4(),
                filename="file.pdf",
                content_type="application/pdf",
                size_bytes=-1,
                fileobj=_fileobj(b""),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()

    def test_oversize_raises_too_large(self):
        uc, _, storage, _ = _make_use_case()
        oversize = MAX_SIZE_BYTES + 1  # 26_214_401

        with pytest.raises(DocumentFileTooLargeError):
            uc.execute(
                project_id=uuid4(),
                filename="huge.pdf",
                content_type="application/pdf",
                size_bytes=oversize,
                fileobj=_fileobj(b"x"),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()

    def test_max_size_exactly_accepted(self):
        uc, repo, storage, session = _make_use_case()

        result = uc.execute(
            project_id=uuid4(),
            filename="max.pdf",
            content_type="application/pdf",
            size_bytes=MAX_SIZE_BYTES,
            fileobj=_fileobj(b"x"),
            uploader_user_id=uuid4(),
        )

        assert result is not None
        storage.put.assert_called_once()


class TestUploadTypeValidation:
    def test_unsupported_extension_raises(self):
        uc, _, storage, _ = _make_use_case()

        with pytest.raises(UnsupportedDocumentTypeError):
            uc.execute(
                project_id=uuid4(),
                filename="malware.exe",
                content_type="application/octet-stream",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

        # storage must NOT be called if type check fails
        storage.put.assert_not_called()

    def test_unsupported_extension_zip_raises(self):
        uc, _, storage, _ = _make_use_case()

        with pytest.raises(UnsupportedDocumentTypeError):
            uc.execute(
                project_id=uuid4(),
                filename="archive.zip",
                content_type="application/zip",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()

    def test_dwg_with_nonstandard_mime_accepted(self):
        """DWG files should be accepted regardless of MIME type — by extension only."""
        uc, repo, storage, session = _make_use_case()

        result = uc.execute(
            project_id=uuid4(),
            filename="blueprint.dwg",
            content_type="application/octet-stream",  # non-standard DWG MIME
            size_bytes=512,
            fileobj=_fileobj(b"x" * 512),
            uploader_user_id=uuid4(),
        )

        assert result is not None
        assert result.filename == "blueprint.dwg"
        storage.put.assert_called_once()

    def test_dwg_with_unknown_mime_accepted(self):
        """DWG should pass even with a completely unknown MIME type."""
        uc, repo, storage, session = _make_use_case()

        result = uc.execute(
            project_id=uuid4(),
            filename="drawing.dwg",
            content_type="application/unknown-mime-type",
            size_bytes=256,
            fileobj=_fileobj(b"x" * 256),
            uploader_user_id=uuid4(),
        )

        assert result is not None
        storage.put.assert_called_once()


class TestUploadDangerousFilename:
    def test_path_traversal_with_valid_ext_sanitized(self):
        """secure_filename('../../etc/passwd.txt') → 'etc_passwd.txt'; storage key is safe.

        NOTE: The domain entity stores the ORIGINAL filename (per spec: "DB row keeps
        the original filename"). The sanitized name is used only for the storage key,
        preventing path-traversal in the object store. Tests verify storage key safety,
        not that the DB filename is sanitized.
        """
        uc, repo, storage, session = _make_use_case()

        # werkzeug secure_filename: '../../etc/passwd.txt' → 'etc_passwd.txt'
        result = uc.execute(
            project_id=uuid4(),
            filename="../../etc/passwd.txt",
            content_type="text/plain",
            size_bytes=100,
            fileobj=_fileobj(b"x" * 100),
            uploader_user_id=uuid4(),
        )

        # DB entity retains original filename (by design — for auditability)
        assert result.filename == "../../etc/passwd.txt"
        # But the storage key must be safe (built from sanitized name)
        storage_key = storage.put.call_args[0][0]
        assert ".." not in storage_key
        assert "passwd" in storage_key.lower()

    def test_storage_key_uses_sanitized_name(self):
        """Storage key must not contain path traversal segments."""
        uc, repo, storage, session = _make_use_case()

        uc.execute(
            project_id=uuid4(),
            filename="../../etc/passwd.txt",
            content_type="text/plain",
            size_bytes=100,
            fileobj=_fileobj(b"x" * 100),
            uploader_user_id=uuid4(),
        )

        # The key arg passed to storage.put must not contain ".."
        put_args = storage.put.call_args[0]
        storage_key = put_args[0]
        assert ".." not in storage_key
        assert "passwd" in storage_key.lower()

    def test_path_traversal_no_extension_raises_unsupported_type(self):
        """'../../etc/passwd' → 'etc_passwd' (no ext) → UnsupportedDocumentTypeError."""
        uc, _, storage, _ = _make_use_case()

        # secure_filename('../../etc/passwd') == 'etc_passwd' — no extension
        # Extension '' is not in ALLOWED_EXTENSIONS → UnsupportedDocumentTypeError
        with pytest.raises(UnsupportedDocumentTypeError):
            uc.execute(
                project_id=uuid4(),
                filename="../../etc/passwd",
                content_type="text/plain",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()

    def test_fully_dangerous_filename_raises_if_nothing_remains(self):
        """If secure_filename produces empty string, UnsupportedDocumentTypeError raised."""
        uc, _, storage, _ = _make_use_case()

        with pytest.raises(UnsupportedDocumentTypeError, match="[Ii]nvalid filename"):
            uc.execute(
                project_id=uuid4(),
                filename="../../",  # reduces to empty after secure_filename
                content_type="application/pdf",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_not_called()


class TestUploadOrphanCleanup:
    def test_db_commit_failure_triggers_storage_delete(self):
        """If DB commit fails after storage.put, storage.delete must be called."""
        repo = _make_repo()
        storage = _make_storage()
        session = _make_session()
        session.commit.side_effect = RuntimeError("DB is down")

        uc = UploadProjectDocumentUseCase(
            repo=repo, storage=storage, db_session=session, filename_sanitizer=WerkzeugFilenameSanitizer()
        )

        with pytest.raises(RuntimeError, match="DB is down"):
            uc.execute(
                project_id=uuid4(),
                filename="report.pdf",
                content_type="application/pdf",
                size_bytes=512,
                fileobj=_fileobj(b"x" * 512),
                uploader_user_id=uuid4(),
            )

        # storage.put was called
        storage.put.assert_called_once()
        # storage.delete must be called with the same key
        storage.delete.assert_called_once()
        put_key = storage.put.call_args[0][0]
        delete_key = storage.delete.call_args[0][0]
        assert put_key == delete_key

    def test_db_save_failure_triggers_storage_delete(self):
        """If repo.save fails (before commit), storage.delete must be called."""
        repo = _make_repo()
        repo.save.side_effect = Exception("constraint violation")
        storage = _make_storage()
        session = _make_session()

        uc = UploadProjectDocumentUseCase(
            repo=repo, storage=storage, db_session=session, filename_sanitizer=WerkzeugFilenameSanitizer()
        )

        with pytest.raises(Exception, match="constraint violation"):
            uc.execute(
                project_id=uuid4(),
                filename="photo.png",
                content_type="image/png",
                size_bytes=256,
                fileobj=_fileobj(b"x" * 256),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_called_once()
        storage.delete.assert_called_once()

    def test_db_commit_failure_exception_propagates(self):
        """The original commit exception must propagate after orphan cleanup."""
        repo = _make_repo()
        storage = _make_storage()
        session = _make_session()
        session.commit.side_effect = ValueError("transaction aborted")

        uc = UploadProjectDocumentUseCase(
            repo=repo, storage=storage, db_session=session, filename_sanitizer=WerkzeugFilenameSanitizer()
        )

        with pytest.raises(ValueError, match="transaction aborted"):
            uc.execute(
                project_id=uuid4(),
                filename="doc.docx",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

    def test_storage_delete_failure_during_cleanup_does_not_swallow_original_error(self):
        """If storage.delete also fails during orphan cleanup, the original DB error still propagates.

        Lines 178-179: the except block around storage.delete logs a warning but does
        not swallow the exception — the outer `raise` re-raises the commit error.
        """
        repo = _make_repo()
        storage = _make_storage()
        storage.delete.side_effect = OSError("storage unreachable")
        session = _make_session()
        session.commit.side_effect = RuntimeError("DB is down")

        uc = UploadProjectDocumentUseCase(
            repo=repo, storage=storage, db_session=session, filename_sanitizer=WerkzeugFilenameSanitizer()
        )

        # The original RuntimeError must propagate despite storage.delete also failing
        with pytest.raises(RuntimeError, match="DB is down"):
            uc.execute(
                project_id=uuid4(),
                filename="report.pdf",
                content_type="application/pdf",
                size_bytes=100,
                fileobj=_fileobj(b"x" * 100),
                uploader_user_id=uuid4(),
            )

        storage.put.assert_called_once()
        storage.delete.assert_called_once()


class TestValidateFileType:
    def test_unsupported_mime_for_known_extension_raises(self):
        """PDF file with a non-allowed, non-octet-stream MIME type → UnsupportedDocumentTypeError.

        This covers line 78: the MIME mismatch branch for non-DWG extensions.
        """
        from app.application.project_documents.upload_project_document import validate_file_type

        with pytest.raises(UnsupportedDocumentTypeError, match="MIME type"):
            validate_file_type("document.pdf", "text/html")  # html MIME for .pdf is disallowed

    def test_octet_stream_mime_for_known_extension_accepted(self):
        """PDF file with application/octet-stream MIME should be accepted (generic MIME pass)."""
        from app.application.project_documents.upload_project_document import validate_file_type

        kind = validate_file_type("document.pdf", "application/octet-stream")
        assert kind == "pdf"

    def test_allowed_mime_for_known_extension_accepted(self):
        """PDF file with application/pdf MIME → 'pdf' kind."""
        from app.application.project_documents.upload_project_document import validate_file_type

        kind = validate_file_type("report.pdf", "application/pdf")
        assert kind == "pdf"
