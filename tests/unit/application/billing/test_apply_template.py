"""Unit tests for ApplyTemplateToCreateDocumentUseCase."""

from uuid import uuid4

import pytest

from app.application.billing.apply_template_to_create_document_usecase import (
    ApplyTemplateToCreateDocumentUseCase,
)
from app.application.billing.dtos import ApplyTemplateInput
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import (
    BillingTemplateNotFoundError,
    ForbiddenBillingDocumentError,
    MissingCompanyProfileError,
)
from tests.unit.application.billing.conftest import make_template


@pytest.fixture
def usecase(doc_repo, template_repo, counter_repo, company_repo, access_repo):
    return ApplyTemplateToCreateDocumentUseCase(
        doc_repo=doc_repo,
        template_repo=template_repo,
        counter_repo=counter_repo,
        project_repo=None,
        company_repo=company_repo,
        access_repo=access_repo,
    )


@pytest.fixture
def devis_template(template_repo, user_id):
    tpl = make_template(user_id=user_id, kind=BillingDocumentKind.DEVIS)
    template_repo.save(tpl)
    return tpl


@pytest.fixture
def facture_template(template_repo, user_id):
    tpl = make_template(user_id=user_id, kind=BillingDocumentKind.FACTURE, name="Facture TPL")
    template_repo.save(tpl)
    return tpl


def _inp(user_id, template_id, company_id=None, recipient="Client Corp", **overrides):
    defaults = dict(
        template_id=template_id,
        user_id=user_id,
        recipient_name=recipient,
        company_id=company_id,
    )
    defaults.update(overrides)
    return ApplyTemplateInput(**defaults)


class TestApplyTemplateHappyPath:
    def test_creates_doc_from_devis_template(
        self, usecase, fake_session, user_id, company_id, seeded_company, devis_template
    ):
        result = usecase.execute(_inp(user_id, devis_template.id, company_id=company_id), fake_session)
        assert result.kind == "devis"
        assert result.status == "draft"
        assert result.recipient_name == "Client Corp"

    def test_items_copied_from_template(
        self, usecase, fake_session, user_id, company_id, seeded_company, devis_template
    ):
        result = usecase.execute(_inp(user_id, devis_template.id, company_id=company_id), fake_session)
        assert len(result.items) == len(devis_template.items)
        assert result.items[0].description == devis_template.items[0].description

    def test_notes_copied_from_template(
        self, usecase, fake_session, user_id, company_id, seeded_company, template_repo
    ):
        from datetime import datetime, timezone
        from uuid import uuid4 as _uuid4
        from app.domain.billing.template import BillingDocumentTemplate
        from tests.unit.application.billing.conftest import make_item

        tpl_with_notes = BillingDocumentTemplate(
            id=_uuid4(),
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="Noted",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            notes="Please pay within 30 days",
            items=(make_item(),),
        )
        template_repo.save(tpl_with_notes)
        result = usecase.execute(_inp(user_id, tpl_with_notes.id, company_id=company_id), fake_session)
        assert result.notes == "Please pay within 30 days"

    def test_facture_from_template(self, usecase, fake_session, user_id, company_id, seeded_company, facture_template):
        result = usecase.execute(_inp(user_id, facture_template.id, company_id=company_id), fake_session)
        assert result.kind == "facture"
        assert result.payment_due_date is not None


class TestApplyTemplateErrors:
    def test_template_not_found_raises(self, usecase, fake_session, user_id, company_id, seeded_company):
        with pytest.raises(BillingTemplateNotFoundError):
            usecase.execute(_inp(user_id, uuid4(), company_id=company_id), fake_session)

    def test_wrong_owner_raises(self, usecase, fake_session, other_user_id, company_id, seeded_company, devis_template):
        # other_user_id tries to apply a template owned by user_id →
        # ForbiddenBillingDocumentError (ownership check before company check)
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(_inp(other_user_id, devis_template.id, company_id=company_id), fake_session)

    def test_company_id_none_with_primary_resolves_automatically(
        self, usecase, fake_session, user_id, company_id, seeded_company, devis_template
    ):
        """H2: company_id=None resolves to caller's primary company.

        seeded_company fixture creates a primary access row for user_id.
        The use-case should find it and use it as issuer.
        """
        result = usecase.execute(_inp(user_id, devis_template.id, company_id=None), fake_session)
        assert result.kind == "devis"
        assert result.company_id == company_id  # resolved from primary

    def test_company_id_none_without_primary_raises(self, usecase, fake_session, user_id, devis_template):
        """H2: company_id=None with no attached company → MissingCompanyProfileError."""
        # user_id has no access rows in this test (seeded_company not used)
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(_inp(user_id, devis_template.id, company_id=None), fake_session)

    def test_unattached_company_raises(self, usecase, fake_session, user_id, devis_template):
        """company_id provided but company doesn't exist → ValueError from ports helper."""
        with pytest.raises((ValueError, MissingCompanyProfileError)):
            usecase.execute(_inp(user_id, devis_template.id, company_id=uuid4()), fake_session)

    def test_empty_recipient_name_raises(
        self, usecase, fake_session, user_id, company_id, seeded_company, devis_template
    ):
        with pytest.raises(ValueError, match="Recipient name"):
            usecase.execute(_inp(user_id, devis_template.id, company_id=company_id, recipient="  "), fake_session)
