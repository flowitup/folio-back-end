"""Unit tests for per-company admin scoping of billing document listing.

Covers the gate that fixes invited members not seeing company billing:
  - company admins see every document of companies they administer
  - company members see only documents they personally own
  - superadmins see all documents regardless of company/owner
"""

from uuid import uuid4

import pytest

from app.application.billing.list_billing_documents_usecase import ListBillingDocumentsUseCase
from app.domain.billing.enums import BillingDocumentKind
from tests.unit.application.billing.conftest import make_access, make_doc


@pytest.fixture
def usecase(doc_repo, access_repo):
    return ListBillingDocumentsUseCase(doc_repo=doc_repo, access_repo=access_repo)


class TestCompanyAdminScoping:
    def test_admin_sees_company_docs_owned_by_others(
        self, usecase, doc_repo, access_repo, user_id, other_user_id, company_id
    ):
        # Caller is admin of company_id; a teammate created the company's docs.
        access_repo.save(make_access(user_id=user_id, company_id=company_id, role="admin"))
        teammate_doc = make_doc(user_id=other_user_id, company_id=company_id)
        doc_repo.save(teammate_doc)

        result = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)

        assert result.total == 1
        assert result.items[0].id == teammate_doc.id

    def test_member_does_not_see_company_docs(
        self, usecase, doc_repo, access_repo, user_id, other_user_id, company_id
    ):
        # Caller is a plain member → must NOT see the company's billing.
        access_repo.save(make_access(user_id=user_id, company_id=company_id, role="member"))
        teammate_doc = make_doc(user_id=other_user_id, company_id=company_id)
        doc_repo.save(teammate_doc)

        result = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)

        assert result.total == 0
        assert result.items == []

    def test_member_still_sees_own_docs(
        self, usecase, doc_repo, access_repo, user_id, company_id
    ):
        access_repo.save(make_access(user_id=user_id, company_id=company_id, role="member"))
        own = make_doc(user_id=user_id, company_id=company_id)
        doc_repo.save(own)

        result = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)

        assert result.total == 1
        assert result.items[0].id == own.id

    def test_superadmin_sees_all_docs(
        self, usecase, doc_repo, user_id, other_user_id, company_id
    ):
        # No access rows for the caller, but is_superadmin lifts all restrictions.
        a = make_doc(user_id=other_user_id, company_id=company_id)
        b = make_doc(user_id=uuid4(), company_id=uuid4(), doc_number="DEV-2026-002")
        doc_repo.save(a)
        doc_repo.save(b)

        result = usecase.execute(
            user_id=user_id, kind=BillingDocumentKind.DEVIS, is_superadmin=True
        )

        assert result.total == 2
