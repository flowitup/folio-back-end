"""Unit tests for GetProjectDocumentUseCase."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.project_documents.get_project_document import GetProjectDocumentUseCase
from app.application.project_documents.ports import IDocumentStorage, IProjectDocumentRepository
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError
from app.domain.project_document import ProjectDocument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(deleted_at=None) -> ProjectDocument:
    return ProjectDocument(
        id=uuid4(),
        project_id=uuid4(),
        uploader_user_id=uuid4(),
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        storage_key="project-documents/test/key/report.pdf",
        created_at=datetime.now(timezone.utc),
        deleted_at=deleted_at,
    )


def _make_repo(doc=None) -> MagicMock:
    repo = MagicMock(spec=IProjectDocumentRepository)
    repo.find_by_id.return_value = doc
    return repo


def _make_storage(data: bytes = b"file content") -> MagicMock:
    storage = MagicMock(spec=IDocumentStorage)
    stream = BytesIO(data)
    storage.get_stream.return_value = (stream, len(data))
    return storage


class TestGetProjectDocumentHappyPath:
    def test_returns_doc_stream_and_length(self):
        doc = _make_doc()
        repo = _make_repo(doc)
        storage = _make_storage(b"hello world")
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)

        result_doc, stream, length = uc.execute(doc.id, doc.project_id)

        assert result_doc is doc
        assert length == len(b"hello world")
        assert stream.read() == b"hello world"

    def test_calls_repo_with_correct_id(self):
        doc = _make_doc()
        repo = _make_repo(doc)
        storage = _make_storage()
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)
        doc_id = doc.id

        uc.execute(doc_id, doc.project_id)

        repo.find_by_id.assert_called_once_with(doc_id)

    def test_calls_storage_with_correct_key(self):
        doc = _make_doc()
        repo = _make_repo(doc)
        storage = _make_storage()
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)

        uc.execute(doc.id, doc.project_id)

        storage.get_stream.assert_called_once_with(doc.storage_key)


class TestGetProjectDocumentNotFound:
    def test_missing_doc_raises_not_found(self):
        repo = _make_repo(doc=None)
        storage = _make_storage()
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(uuid4(), uuid4())

        # Storage must NOT be called
        storage.get_stream.assert_not_called()

    def test_soft_deleted_doc_raises_not_found(self):
        deleted_doc = _make_doc(deleted_at=datetime.now(timezone.utc))
        repo = _make_repo(doc=deleted_doc)
        storage = _make_storage()
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(deleted_doc.id, deleted_doc.project_id)

        # Storage must NOT be called for soft-deleted docs
        storage.get_stream.assert_not_called()

    def test_raises_when_doc_belongs_to_different_project(self):
        """Cross-project guard must fire BEFORE storage.get_stream is called.

        An attacker who is a member of project B should not be able to exhaust
        backend→storage TCP connections by probing project-A document UUIDs.
        """
        doc = _make_doc()
        repo = _make_repo(doc)
        storage = _make_storage()
        uc = GetProjectDocumentUseCase(repo=repo, storage=storage)

        wrong_project_id = uuid4()
        assert wrong_project_id != doc.project_id  # sanity

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(doc.id, wrong_project_id)

        # Storage must NOT be opened — guard must short-circuit before stream fetch
        storage.get_stream.assert_not_called()
