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
def usecase(doc_repo, template_repo, counter_repo, profile_repo):
    return ApplyTemplateToCreateDocumentUseCase(
        doc_repo=doc_repo,
        template_repo=template_repo,
        counter_repo=counter_repo,
        profile_repo=profile_repo,
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


def _inp(user_id, template_id, recipient="Client Corp", **overrides):
    defaults = dict(
        template_id=template_id,
        user_id=user_id,
        recipient_name=recipient,
    )
    defaults.update(overrides)
    return ApplyTemplateInput(**defaults)


class TestApplyTemplateHappyPath:
    def test_creates_doc_from_devis_template(self, usecase, fake_session, user_id, profile, devis_template):
        result = usecase.execute(_inp(user_id, devis_template.id), fake_session)
        assert result.kind == "devis"
        assert result.status == "draft"
        assert result.recipient_name == "Client Corp"

    def test_items_copied_from_template(self, usecase, fake_session, user_id, profile, devis_template):
        result = usecase.execute(_inp(user_id, devis_template.id), fake_session)
        assert len(result.items) == len(devis_template.items)
        assert result.items[0].description == devis_template.items[0].description

    def test_notes_copied_from_template(self, usecase, fake_session, user_id, profile, template_repo):
        from datetime import datetime, timezone
        from uuid import uuid4
        from app.domain.billing.template import BillingDocumentTemplate
        from tests.unit.application.billing.conftest import make_item

        tpl_with_notes = BillingDocumentTemplate(
            id=uuid4(),
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="Noted",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            notes="Please pay within 30 days",
            items=(make_item(),),
        )
        template_repo.save(tpl_with_notes)
        result = usecase.execute(_inp(user_id, tpl_with_notes.id), fake_session)
        assert result.notes == "Please pay within 30 days"

    def test_facture_from_template(self, usecase, fake_session, user_id, profile, facture_template):
        result = usecase.execute(_inp(user_id, facture_template.id), fake_session)
        assert result.kind == "facture"
        assert result.payment_due_date is not None


class TestApplyTemplateErrors:
    def test_template_not_found_raises(self, usecase, fake_session, user_id, profile):
        with pytest.raises(BillingTemplateNotFoundError):
            usecase.execute(_inp(user_id, uuid4()), fake_session)

    def test_wrong_owner_raises(self, usecase, fake_session, other_user_id, profile, devis_template):
        # other_user_id tries to apply a template owned by user_id
        # but profile_repo has no profile for other_user_id, so we expect
        # ForbiddenBillingDocumentError (ownership check comes first)
        with pytest.raises(ForbiddenBillingDocumentError):
            usecase.execute(_inp(other_user_id, devis_template.id), fake_session)

    def test_missing_profile_raises(self, usecase, fake_session, user_id, devis_template):
        # profile_repo is empty — no profile for user_id
        with pytest.raises(MissingCompanyProfileError):
            usecase.execute(_inp(user_id, devis_template.id), fake_session)

    def test_empty_recipient_name_raises(self, usecase, fake_session, user_id, profile, devis_template):
        with pytest.raises(ValueError, match="Recipient name"):
            usecase.execute(_inp(user_id, devis_template.id, recipient="  "), fake_session)
