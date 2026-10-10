"""The partial unique indexes the migrations create are declared on the models too.

The test schema is built with ``create_all()`` from the models, so a rule that
lived only in a migration was never enforced in tests (and the next autogenerate
would have proposed dropping it). tests/migrations/test_models_match_migrations.py
checks the full model/migration match on Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.infrastructure.database.models import (
    CompanyModel,
    PaymentMethodModel,
    UserModel,
)

# ux_payment_methods_company_label_active is an expression index, which SQLite
# reflection skips; the label test below covers it. uix_user_company_access_primary_per_user
# is deliberately a no-op on SQLite (see the model) and is checked on Postgres only.
_UNIQUE_RULES = {
    "billing_documents": "uix_billing_documents_source_devis_id",
    "company_member_grants": "ix_company_member_grants_company_wide_unique",
    "company_persons": "ix_company_persons_company_phone_unique",
    "invitations": "uq_invitations_pending_email_project",
}


@pytest.mark.parametrize("table,index", sorted(_UNIQUE_RULES.items()))
def test_business_rule_index_is_built_from_the_models(engine, tables, table, index):
    found = {ix["name"]: ix for ix in inspect(engine).get_indexes(table)}
    assert index in found
    assert found[index]["unique"]


def _user_and_company(session):
    user = UserModel(id=uuid4(), email=f"rules-{uuid4().hex[:8]}@test.com", is_active=True)
    session.add(user)
    session.flush()
    now = datetime.now(timezone.utc)
    company = CompanyModel(
        id=uuid4(), legal_name="Rules Co", address="1 rue", created_by=user.id, created_at=now, updated_at=now
    )
    session.add(company)
    session.flush()
    return user, company


def test_an_active_label_is_unique_per_company_ignoring_case(session):
    user, company = _user_and_company(session)
    session.add(PaymentMethodModel(company_id=company.id, label="Carte Pro", created_by=user.id, is_active=False))
    session.add(PaymentMethodModel(company_id=company.id, label="Carte Pro", created_by=user.id))
    session.flush()  # an inactive duplicate is fine

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(PaymentMethodModel(company_id=company.id, label="carte pro", created_by=user.id))
        session.flush()
