"""Unit tests for DeleteProjectDocumentUseCase."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.application.project_documents.delete_project_document import DeleteProjectDocumentUseCase
from app.application.project_documents.exceptions import DocumentPermissionDeniedError
from app.application.project_documents.ports import IProjectDocumentRepository
from app.domain.exceptions.project_document_exceptions import ProjectDocumentNotFoundError
from app.domain.project_document import ProjectDocument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc(project_id=None, uploader_user_id=None, deleted_at=None) -> ProjectDocument:
    return ProjectDocument(
        id=uuid4(),
        project_id=project_id or uuid4(),
        uploader_user_id=uploader_user_id or uuid4(),
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        storage_key="project-documents/x/y/report.pdf",
        created_at=datetime.now(timezone.utc),
        deleted_at=deleted_at,
    )


def _make_project(project_id=None, owner_id=None):
    """Return a simple namespace mimicking the project domain entity."""
    return SimpleNamespace(
        id=project_id or uuid4(),
        owner_id=owner_id or uuid4(),
    )


def _make_repo(doc=None) -> MagicMock:
    repo = MagicMock(spec=IProjectDocumentRepository)
    repo.find_by_id.return_value = doc
    return repo


def _make_session() -> MagicMock:
    session = MagicMock()
    session.commit = MagicMock()
    return session


def _make_use_case(repo=None, session=None):
    repo = repo or _make_repo()
    session = session or _make_session()
    return DeleteProjectDocumentUseCase(repo=repo, db_session=session), repo, session


class TestDeleteNotFound:
    def test_missing_doc_raises_not_found(self):
        uc, repo, _ = _make_use_case(repo=_make_repo(doc=None))
        project = _make_project()

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(uuid4(), uuid4(), project)

    def test_soft_deleted_doc_raises_not_found(self):
        project_id = uuid4()
        doc = _make_doc(project_id=project_id, deleted_at=datetime.now(timezone.utc))
        project = _make_project(project_id=project_id)
        uc, _, _ = _make_use_case(repo=_make_repo(doc=doc))

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(doc.id, doc.uploader_user_id, project)

    def test_cross_project_doc_raises_not_found(self):
        """Doc from different project must NOT be leaked — maps to NotFound."""
        doc = _make_doc(project_id=uuid4())  # doc belongs to project A
        project = _make_project(project_id=uuid4())  # request is for project B

        uc, _, _ = _make_use_case(repo=_make_repo(doc=doc))

        with pytest.raises(ProjectDocumentNotFoundError):
            uc.execute(doc.id, doc.uploader_user_id, project)


class TestDeletePermissions:
    def test_uploader_can_delete_own_doc(self):
        project_id = uuid4()
        uploader_id = uuid4()
        doc = _make_doc(project_id=project_id, uploader_user_id=uploader_id)
        project = _make_project(project_id=project_id)
        uc, repo, session = _make_use_case(repo=_make_repo(doc=doc))

        uc.execute(doc.id, uploader_id, project)

        repo.soft_delete.assert_called_once()
        session.commit.assert_called_once()

    def test_project_owner_can_delete_any_doc(self):
        project_id = uuid4()
        owner_id = uuid4()
        uploader_id = uuid4()  # different user
        doc = _make_doc(project_id=project_id, uploader_user_id=uploader_id)
        project = _make_project(project_id=project_id, owner_id=owner_id)
        uc, repo, session = _make_use_case(repo=_make_repo(doc=doc))

        uc.execute(doc.id, owner_id, project, is_admin=False)

        repo.soft_delete.assert_called_once()
        session.commit.assert_called_once()

    def test_admin_can_delete_any_doc(self):
        project_id = uuid4()
        admin_id = uuid4()
        owner_id = uuid4()  # different
        uploader_id = uuid4()  # different
        doc = _make_doc(project_id=project_id, uploader_user_id=uploader_id)
        project = _make_project(project_id=project_id, owner_id=owner_id)
        uc, repo, session = _make_use_case(repo=_make_repo(doc=doc))

        uc.execute(doc.id, admin_id, project, is_admin=True)

        repo.soft_delete.assert_called_once()
        session.commit.assert_called_once()

    def test_member_non_uploader_cannot_delete(self):
        project_id = uuid4()
        uploader_id = uuid4()
        requester_id = uuid4()  # member, but NOT uploader or owner
        owner_id = uuid4()  # different from requester
        doc = _make_doc(project_id=project_id, uploader_user_id=uploader_id)
        project = _make_project(project_id=project_id, owner_id=owner_id)
        uc, repo, _ = _make_use_case(repo=_make_repo(doc=doc))

        with pytest.raises(DocumentPermissionDeniedError):
            uc.execute(doc.id, requester_id, project, is_admin=False)

        repo.soft_delete.assert_not_called()

    def test_soft_delete_called_with_doc_id_and_timestamp(self):
        project_id = uuid4()
        uploader_id = uuid4()
        doc = _make_doc(project_id=project_id, uploader_user_id=uploader_id)
        project = _make_project(project_id=project_id)
        uc, repo, _ = _make_use_case(repo=_make_repo(doc=doc))

        uc.execute(doc.id, uploader_id, project)

        call_args = repo.soft_delete.call_args[0]
        assert call_args[0] == doc.id
        assert isinstance(call_args[1], datetime)
