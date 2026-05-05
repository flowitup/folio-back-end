"""Unit tests for ListBillingDocumentsUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.list_billing_documents_usecase import ListBillingDocumentsUseCase
from app.domain.billing.enums import BillingDocumentKind, BillingDocumentStatus
from tests.unit.application.billing.conftest import make_doc


@pytest.fixture
def usecase(doc_repo):
    return ListBillingDocumentsUseCase(doc_repo=doc_repo)


class TestListBillingDocuments:
    def test_empty_returns_empty(self, usecase, user_id):
        result = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        assert result.items == []
        assert result.total == 0

    def test_lists_only_own_docs(self, usecase, doc_repo, user_id, other_user_id):
        mine = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        other = make_doc(user_id=other_user_id, kind=BillingDocumentKind.DEVIS)
        doc_repo.save(mine)
        doc_repo.save(other)
        result = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        assert result.total == 1
        assert result.items[0].id == mine.id

    def test_filter_by_kind(self, usecase, doc_repo, user_id):
        devis = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        facture = make_doc(user_id=user_id, kind=BillingDocumentKind.FACTURE, doc_number="FAC-2026-001")
        doc_repo.save(devis)
        doc_repo.save(facture)

        result_devis = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        result_facture = usecase.execute(user_id=user_id, kind=BillingDocumentKind.FACTURE)

        assert result_devis.total == 1
        assert result_facture.total == 1

    def test_filter_by_status(self, usecase, doc_repo, user_id):
        draft = make_doc(user_id=user_id, status=BillingDocumentStatus.DRAFT)
        sent = make_doc(user_id=user_id, status=BillingDocumentStatus.SENT, doc_number="DEV-2026-002")
        doc_repo.save(draft)
        doc_repo.save(sent)

        result = usecase.execute(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.SENT,
        )
        assert result.total == 1
        assert result.items[0].status == "sent"

    def test_filter_by_project_id(self, usecase, doc_repo, user_id):
        pid = uuid4()
        with_project = make_doc(user_id=user_id, project_id=pid)
        without_project = make_doc(user_id=user_id, doc_number="DEV-2026-002")
        doc_repo.save(with_project)
        doc_repo.save(without_project)

        result = usecase.execute(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            project_id=pid,
        )
        assert result.total == 1
        assert result.items[0].project_id == pid

    def test_test_list_paginated_filtered_by_status_kind_project(self, usecase, doc_repo, user_id):
        """Spec #15: paginated, filtered by status + kind + project_id."""
        pid = uuid4()
        docs = []
        for i in range(5):
            d = make_doc(
                user_id=user_id,
                kind=BillingDocumentKind.DEVIS,
                status=BillingDocumentStatus.SENT,
                project_id=pid,
                doc_number=f"DEV-2026-{i+1:03d}",
            )
            doc_repo.save(d)
            docs.append(d)
        # One that does NOT match
        no_match = make_doc(user_id=user_id, kind=BillingDocumentKind.DEVIS, doc_number="DEV-2026-099")
        doc_repo.save(no_match)

        result = usecase.execute(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.SENT,
            project_id=pid,
            limit=3,
            offset=0,
        )
        assert result.total == 5
        assert len(result.items) == 3

        page2 = usecase.execute(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            status=BillingDocumentStatus.SENT,
            project_id=pid,
            limit=3,
            offset=3,
        )
        assert len(page2.items) == 2

    def test_pagination_limit_offset(self, usecase, doc_repo, user_id):
        for i in range(5):
            d = make_doc(user_id=user_id, doc_number=f"DEV-2026-{i+1:03d}")
            doc_repo.save(d)

        page1 = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS, limit=2, offset=0)
        page2 = usecase.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS, limit=2, offset=2)

        assert len(page1.items) == 2
        assert len(page2.items) == 2
        assert {d.id for d in page1.items}.isdisjoint({d.id for d in page2.items})
