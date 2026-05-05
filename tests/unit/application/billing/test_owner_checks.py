"""Unit tests for ownership enforcement across billing use-cases.

Verifies that ForbiddenBillingDocumentError is raised when a user
attempts to access a document or template they do not own.
"""

from uuid import uuid4

import pytest

from app.application.billing.delete_billing_document_usecase import DeleteBillingDocumentUseCase
from app.application.billing.get_billing_document_usecase import GetBillingDocumentUseCase
from app.domain.billing.exceptions import BillingDocumentNotFoundError, ForbiddenBillingDocumentError
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def get_uc(doc_repo):
    return GetBillingDocumentUseCase(doc_repo=doc_repo)


@pytest.fixture
def delete_uc(doc_repo):
    return DeleteBillingDocumentUseCase(doc_repo=doc_repo)


@pytest.fixture
def owned_doc(doc_repo, user_id):
    doc = make_doc(user_id=user_id)
    doc_repo.save(doc)
    return doc


class TestGetBillingDocument:
    def test_owner_can_get(self, get_uc, user_id, owned_doc):
        result = get_uc.execute(owned_doc.id, user_id)
        assert result.id == owned_doc.id

    def test_not_found_raises(self, get_uc, user_id):
        with pytest.raises(BillingDocumentNotFoundError):
            get_uc.execute(uuid4(), user_id)

    def test_other_user_raises_forbidden(self, get_uc, other_user_id, owned_doc):
        """Owner isolation: user B cannot see user A's docs → ForbiddenBillingDocumentError.

        The API layer maps this to 404 (not 403) to avoid existence leaks.
        """
        with pytest.raises(ForbiddenBillingDocumentError):
            get_uc.execute(owned_doc.id, other_user_id)


class TestDeleteBillingDocument:
    def test_owner_can_delete(self, delete_uc, doc_repo, fake_session, user_id, owned_doc):
        delete_uc.execute(owned_doc.id, user_id, fake_session)
        assert doc_repo.find_by_id(owned_doc.id) is None

    def test_not_found_raises(self, delete_uc, fake_session, user_id):
        with pytest.raises(BillingDocumentNotFoundError):
            delete_uc.execute(uuid4(), user_id, fake_session)

    def test_other_user_raises_forbidden(self, delete_uc, fake_session, other_user_id, owned_doc):
        with pytest.raises(ForbiddenBillingDocumentError):
            delete_uc.execute(owned_doc.id, other_user_id, fake_session)
