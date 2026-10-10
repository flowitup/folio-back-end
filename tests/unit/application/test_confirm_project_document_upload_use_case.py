"""Unit tests for ConfirmProjectDocumentUploadUseCase: the presigned PUT binds no size
or type, so confirm re-applies the multipart rules to what landed in storage."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.project_documents.confirm_project_document_upload import (
    ConfirmProjectDocumentUploadUseCase,
    StorageKeyMismatchError,
)
from app.application.project_documents.exceptions import (
    DocumentFileTooLargeError,
    EmptyFileError,
    UnsupportedDocumentTypeError,
)
from app.application.project_documents.ports import IDocumentStorage, IProjectDocumentRepository
from app.application.project_documents.upload_project_document import MAX_SIZE_BYTES
from app.infrastructure.adapters.werkzeug_filename_sanitizer import WerkzeugFilenameSanitizer


def _use_case(content_length=10):
    repo = MagicMock(spec=IProjectDocumentRepository)
    repo.save.side_effect = lambda doc: doc
    storage = MagicMock(spec=IDocumentStorage)
    storage.head_object.return_value = {"ContentLength": content_length}
    uc = ConfirmProjectDocumentUploadUseCase(
        repo=repo, storage=storage, db_session=MagicMock(), filename_sanitizer=WerkzeugFilenameSanitizer()
    )
    return uc, repo, storage


def _confirm(uc, *, filename="plan.pdf", content_type="application/pdf", key=None):
    project_id, doc_id = uuid4(), uuid4()
    key = key or f"project-documents/{project_id}/{doc_id}/{filename}"
    return uc.execute(
        project_id=project_id,
        doc_id=doc_id,
        storage_key=key,
        filename=filename,
        content_type=content_type,
        size_bytes=10,
        uploader_user_id=uuid4(),
    )


def test_a_valid_upload_is_saved_with_the_stored_size():
    uc, repo, _ = _use_case(content_length=2048)
    doc = _confirm(uc)
    assert doc.size_bytes == 2048
    repo.save.assert_called_once()


def test_a_disallowed_type_is_refused_before_anything_is_saved():
    uc, repo, _ = _use_case()
    with pytest.raises(UnsupportedDocumentTypeError):
        _confirm(uc, filename="evil.svg", content_type="image/svg+xml")
    repo.save.assert_not_called()


def test_a_filename_other_than_the_presigned_one_is_refused():
    uc, repo, _ = _use_case()
    project_id, doc_id = uuid4(), uuid4()
    with pytest.raises(StorageKeyMismatchError):
        uc.execute(
            project_id=project_id,
            doc_id=doc_id,
            storage_key=f"project-documents/{project_id}/{doc_id}/plan.pdf",
            filename="other.pdf",
            content_type="application/pdf",
            size_bytes=10,
            uploader_user_id=uuid4(),
        )
    repo.save.assert_not_called()


def test_an_oversized_object_is_refused_and_deleted():
    uc, repo, storage = _use_case(content_length=MAX_SIZE_BYTES + 1)
    with pytest.raises(DocumentFileTooLargeError):
        _confirm(uc)
    storage.delete.assert_called_once()
    repo.save.assert_not_called()


def test_an_empty_object_is_refused_and_deleted():
    uc, repo, storage = _use_case(content_length=0)
    with pytest.raises(EmptyFileError):
        _confirm(uc)
    storage.delete.assert_called_once()
    repo.save.assert_not_called()


def test_the_stored_filename_drops_control_characters_and_keeps_the_presigned_key():
    uc, repo, _ = _use_case()
    project_id, doc_id = uuid4(), uuid4()
    doc = uc.execute(
        project_id=project_id,
        doc_id=doc_id,
        # presign keyed the object on the sanitized original name
        storage_key=f"project-documents/{project_id}/{doc_id}/Plan_X.pdf",
        filename="Plan\nX.pdf",
        content_type="application/pdf",
        size_bytes=10,
        uploader_user_id=uuid4(),
    )
    assert doc.filename == "PlanX.pdf"


def test_an_unlisted_dwg_mime_is_stored_as_the_generic_type():
    uc, repo, _ = _use_case()
    doc = _confirm(uc, filename="plan.dwg", content_type="application/" + "x" * 300)
    assert doc.content_type == "application/octet-stream"
