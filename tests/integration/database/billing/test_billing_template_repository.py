"""Integration tests for SqlAlchemyBillingTemplateRepository against SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.domain.billing.enums import BillingDocumentKind
from app.domain.billing.template import BillingDocumentTemplate
from app.domain.billing.value_objects import BillingDocumentItem
from app.infrastructure.database.models import UserModel
from app.infrastructure.database.repositories.sqlalchemy_billing_template_repository import (
    SqlAlchemyBillingTemplateRepository,
)


def _seed_user(session) -> UUID:
    user = UserModel(
        id=uuid4(),
        email=f"tpl-{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return UUID(str(user.id))


def _make_template(
    user_id: UUID,
    kind: BillingDocumentKind = BillingDocumentKind.DEVIS,
    name: str = "Test Template",
) -> BillingDocumentTemplate:
    now = datetime.now(timezone.utc)
    return BillingDocumentTemplate(
        id=uuid4(),
        user_id=user_id,
        kind=kind,
        name=name,
        created_at=now,
        updated_at=now,
        items=(
            BillingDocumentItem(
                description="Service",
                quantity=Decimal("1"),
                unit_price=Decimal("200"),
                vat_rate=Decimal("20"),
            ),
        ),
    )


class TestBillingTemplateRepositoryCRUD:
    def test_save_and_find_by_id(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        tpl = _make_template(user_id)
        saved = repo.save(tpl)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert found is not None
        assert found.name == "Test Template"
        assert found.kind == BillingDocumentKind.DEVIS

    def test_find_by_id_missing_returns_none(self, session):
        repo = SqlAlchemyBillingTemplateRepository(session)
        assert repo.find_by_id(uuid4()) is None

    def test_update_via_save(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        tpl = _make_template(user_id)
        saved = repo.save(tpl)
        updated = saved.with_updates(name="Updated Name")
        repo.save(updated)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert found.name == "Updated Name"

    def test_delete(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        tpl = _make_template(user_id)
        saved = repo.save(tpl)
        repo.delete(UUID(str(saved.id)))
        session.flush()
        assert repo.find_by_id(UUID(str(saved.id))) is None

    def test_list_for_user_all_kinds(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        devis_tpl = _make_template(user_id, kind=BillingDocumentKind.DEVIS, name="Devis TPL")
        facture_tpl = _make_template(user_id, kind=BillingDocumentKind.FACTURE, name="Facture TPL")
        repo.save(devis_tpl)
        repo.save(facture_tpl)

        all_tpls = repo.list_for_user(user_id)
        assert len(all_tpls) == 2

    def test_list_for_user_filter_by_kind(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        repo.save(_make_template(user_id, kind=BillingDocumentKind.DEVIS, name="D"))
        repo.save(_make_template(user_id, kind=BillingDocumentKind.FACTURE, name="F"))

        devis_only = repo.list_for_user(user_id, kind=BillingDocumentKind.DEVIS)
        assert len(devis_only) == 1
        assert devis_only[0].kind == BillingDocumentKind.DEVIS

    def test_list_isolates_users(self, session):
        user_a = _seed_user(session)
        user_b = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        repo.save(_make_template(user_a, name="A TPL"))
        repo.save(_make_template(user_b, name="B TPL"))

        a_tpls = repo.list_for_user(user_a)
        assert len(a_tpls) == 1
        assert a_tpls[0].name == "A TPL"

    def test_items_round_trip(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyBillingTemplateRepository(session)
        tpl = _make_template(user_id)
        saved = repo.save(tpl)
        found = repo.find_by_id(UUID(str(saved.id)))
        assert len(found.items) == 1
        assert found.items[0].description == "Service"
        assert found.items[0].quantity == Decimal("1")
