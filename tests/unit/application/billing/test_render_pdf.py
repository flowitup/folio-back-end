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
