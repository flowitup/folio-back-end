"""Unit tests for RenderBillingDocumentPdfUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.render_billing_document_pdf_usecase import RenderBillingDocumentPdfUseCase
from app.domain.billing.exceptions import BillingDocumentNotFoundError, ForbiddenBillingDocumentError
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo, pdf_renderer):
    return RenderBillingDocumentPdfUseCase(doc_repo=doc_repo, pdf_renderer=pdf_renderer)


@pytest.fixture
def saved_doc(doc_repo, user_id):
    doc = make_doc(user_id=user_id)
    doc_repo.save(doc)
    return doc


class TestRenderPdfHappyPath:
    def test_returns_pdf_bytes(self, usecase, user_id, saved_doc):
        result = usecase.execute(saved_doc.id, user_id)
        assert result.content == b"%PDF-1.4 fake"

    def test_filename_uses_document_number(self, usecase, user_id, saved_doc):
        result = usecase.execute(saved_doc.id, user_id)
        assert result.filename == f"{saved_doc.document_number}.pdf"


class TestRenderPdfErrors:
    def test_not_found_raises(self, usecase, user_id):
        with pytest.raises(BillingDocumentNotFoundError):
            usecase.execute(uuid4(), user_id)

    def test_wrong_owner_raises(self, usecase, other_user_id, saved_doc):
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(saved_doc.id, other_user_id)


class _ProjectRepo:
    def __init__(self, *projects):
        self._projects = {p.id: p for p in projects}

    def find_by_id(self, project_id):
        return self._projects.get(project_id)


class TestRenderPdfLinkedProject:
    """The linked project is handed to the renderer, which prints it as the document's Objet."""

    def _project(self):
        from datetime import datetime, timezone

        from app.domain.entities.project import Project

        return Project(
            id=uuid4(),
            name="Chantier Lilas",
            owner_id=uuid4(),
            created_at=datetime.now(timezone.utc),
            address="5 rue des Lilas",
        )

    def test_renderer_receives_the_linked_project(self, doc_repo, pdf_renderer, user_id):
        project = self._project()
        doc = make_doc(user_id=user_id, project_id=project.id)
        doc_repo.save(doc)
        uc = RenderBillingDocumentPdfUseCase(
            doc_repo=doc_repo, pdf_renderer=pdf_renderer, project_repo=_ProjectRepo(project)
        )
        uc.execute(doc.id, user_id)
        assert pdf_renderer.projects == [project]

    def test_no_project_without_a_link_or_once_the_project_is_gone(self, doc_repo, pdf_renderer, user_id):
        unlinked = make_doc(user_id=user_id)
        gone = make_doc(user_id=user_id, project_id=uuid4())
        doc_repo.save(unlinked)
        doc_repo.save(gone)
        uc = RenderBillingDocumentPdfUseCase(
            doc_repo=doc_repo, pdf_renderer=pdf_renderer, project_repo=_ProjectRepo(self._project())
        )
        uc.execute(unlinked.id, user_id)
        uc.execute(gone.id, user_id)
        assert pdf_renderer.projects == [None, None]
