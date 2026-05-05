"""Unit tests for template use-cases: Create, Get, Update, Delete, List."""

from decimal import Decimal
from uuid import uuid4

import pytest

from app.application.billing.create_template_usecase import CreateTemplateUseCase
from app.application.billing.delete_template_usecase import DeleteTemplateUseCase
from app.application.billing.get_template_usecase import GetTemplateUseCase
from app.application.billing.list_templates_usecase import ListTemplatesUseCase
from app.application.billing.update_template_usecase import UpdateTemplateUseCase
from app.application.billing.dtos import CreateTemplateInput, ItemInput, UpdateTemplateInput
from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.exceptions import BillingTemplateNotFoundError, ForbiddenBillingDocumentError
from tests.unit.application.billing.conftest import make_template


@pytest.fixture
def create_uc(template_repo):
    return CreateTemplateUseCase(template_repo=template_repo)


@pytest.fixture
def get_uc(template_repo):
    return GetTemplateUseCase(template_repo=template_repo)


@pytest.fixture
def update_uc(template_repo):
    return UpdateTemplateUseCase(template_repo=template_repo)


@pytest.fixture
def delete_uc(template_repo):
    return DeleteTemplateUseCase(template_repo=template_repo)


@pytest.fixture
def list_uc(template_repo):
    return ListTemplatesUseCase(template_repo=template_repo)


@pytest.fixture
def saved_template(template_repo, user_id):
    tpl = make_template(user_id=user_id)
    template_repo.save(tpl)
    return tpl


class TestCreateTemplate:
    def test_create_devis_template(self, create_uc, fake_session, user_id):
        inp = CreateTemplateInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="Standard Devis",
            items=[
                ItemInput(
                    description="Service", quantity=Decimal("1"), unit_price=Decimal("100"), vat_rate=Decimal("20")
                )
            ],
        )
        result = create_uc.execute(inp, fake_session)
        assert result.kind == "devis"
        assert result.name == "Standard Devis"
        assert len(result.items) == 1

    def test_create_template_without_items(self, create_uc, fake_session, user_id):
        inp = CreateTemplateInput(
            user_id=user_id,
            kind=BillingDocumentKind.FACTURE,
            name="Empty Template",
        )
        result = create_uc.execute(inp, fake_session)
        assert result.items == []

    def test_empty_name_raises(self, create_uc, fake_session, user_id):
        inp = CreateTemplateInput(
            user_id=user_id,
            kind=BillingDocumentKind.DEVIS,
            name="  ",
        )
        with pytest.raises(ValueError, match="name"):
            create_uc.execute(inp, fake_session)


class TestGetTemplate:
    def test_get_existing(self, get_uc, user_id, saved_template):
        result = get_uc.execute(saved_template.id, user_id)
        assert result.id == saved_template.id

    def test_not_found_raises(self, get_uc, user_id):
        with pytest.raises(BillingTemplateNotFoundError):
            get_uc.execute(uuid4(), user_id)

    def test_wrong_owner_raises(self, get_uc, other_user_id, saved_template):
        with pytest.raises(ForbiddenBillingDocumentError):
            get_uc.execute(saved_template.id, other_user_id)


class TestUpdateTemplate:
    def test_update_name(self, update_uc, fake_session, user_id, saved_template):
        inp = UpdateTemplateInput(id=saved_template.id, user_id=user_id, name="New Name")
        result = update_uc.execute(inp, fake_session)
        assert result.name == "New Name"

    def test_update_empty_name_raises(self, update_uc, fake_session, user_id, saved_template):
        """Empty name after strip → ValueError (line 39)."""
        inp = UpdateTemplateInput(id=saved_template.id, user_id=user_id, name="  ")
        with pytest.raises(ValueError, match="name"):
            update_uc.execute(inp, fake_session)

    def test_update_items_notes_terms_vat(self, update_uc, fake_session, user_id, saved_template):
        """Covers items, notes, terms, default_vat_rate branches (lines 43, 46, 49, 52)."""
        new_items = [
            ItemInput(description="Updated", quantity=Decimal("3"), unit_price=Decimal("50"), vat_rate=Decimal("10"))
        ]
        inp = UpdateTemplateInput(
            id=saved_template.id,
            user_id=user_id,
            items=new_items,
            notes="Notes updated",
            terms="Terms updated",
            default_vat_rate=Decimal("10"),
        )
        result = update_uc.execute(inp, fake_session)
        assert len(result.items) == 1
        assert result.notes == "Notes updated"
        assert result.terms == "Terms updated"
        assert result.default_vat_rate == Decimal("10")

    def test_not_found_raises(self, update_uc, fake_session, user_id):
        inp = UpdateTemplateInput(id=uuid4(), user_id=user_id, name="X")
        with pytest.raises(BillingTemplateNotFoundError):
            update_uc.execute(inp, fake_session)

    def test_wrong_owner_raises(self, update_uc, fake_session, other_user_id, saved_template):
        inp = UpdateTemplateInput(id=saved_template.id, user_id=other_user_id, name="X")
        with pytest.raises(ForbiddenBillingDocumentError):
            update_uc.execute(inp, fake_session)


class TestDeleteTemplate:
    def test_delete_removes_from_repo(self, delete_uc, template_repo, fake_session, user_id, saved_template):
        delete_uc.execute(saved_template.id, user_id, fake_session)
        assert template_repo.find_by_id(saved_template.id) is None

    def test_not_found_raises(self, delete_uc, fake_session, user_id):
        with pytest.raises(BillingTemplateNotFoundError):
            delete_uc.execute(uuid4(), user_id, fake_session)

    def test_wrong_owner_raises(self, delete_uc, fake_session, other_user_id, saved_template):
        with pytest.raises(ForbiddenBillingDocumentError):
            delete_uc.execute(saved_template.id, other_user_id, fake_session)


class TestListTemplates:
    def test_empty(self, list_uc, user_id):
        result = list_uc.execute(user_id=user_id)
        assert result == []

    def test_lists_own_templates(self, list_uc, template_repo, user_id, other_user_id):
        mine = make_template(user_id=user_id)
        others = make_template(user_id=other_user_id, name="Other")
        template_repo.save(mine)
        template_repo.save(others)
        result = list_uc.execute(user_id=user_id)
        assert len(result) == 1
        assert result[0].id == mine.id

    def test_filter_by_kind(self, list_uc, template_repo, user_id):
        devis_tpl = make_template(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        facture_tpl = make_template(user_id=user_id, kind=BillingDocumentKind.FACTURE, name="F")
        template_repo.save(devis_tpl)
        template_repo.save(facture_tpl)

        devis_result = list_uc.execute(user_id=user_id, kind=BillingDocumentKind.DEVIS)
        facture_result = list_uc.execute(user_id=user_id, kind=BillingDocumentKind.FACTURE)

        assert len(devis_result) == 1
        assert len(facture_result) == 1
