"""Phase 2 — billing_document_templates.company_id backfill (shared with migration 2ca24be9e3a8).

Mirrors tests/unit/infrastructure/test_backfill_projects_company_id.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.infrastructure.database.backfills.billing_templates_company import (
    backfill_from_primary_company,
    backfill_from_sole_company,
    count_null_company_id,
    run_backfill,
)
from app.infrastructure.database.models import BillingDocumentTemplateModel, UserModel
from app.infrastructure.database.models.company import CompanyModel
from app.infrastructure.database.models.user_company_access import UserCompanyAccessModel

PASSWORD_HASH = "x" * 60


def _make_user(session, email: str) -> UserModel:
    u = UserModel(id=uuid4(), email=email, password_hash=PASSWORD_HASH, is_active=True)
    session.add(u)
    return u


def _make_company(session, created_by) -> CompanyModel:
    now = datetime.now(timezone.utc)
    c = CompanyModel(
        id=uuid4(),
        legal_name=f"Co {uuid4().hex[:6]}",
        address="1 rue X",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(c)
    return c


def _make_template(owner_id, name: str = "Tpl") -> BillingDocumentTemplateModel:
    now = datetime.now(timezone.utc)
    return BillingDocumentTemplateModel(
        id=uuid4(),
        user_id=owner_id,
        kind="devis",
        name=name,
        items=[],
        created_at=now,
        updated_at=now,
    )


def test_backfill_from_primary_company_fills_orphaned_template(session):
    owner = _make_user(session, "tpl-owner-primary@test.com")
    session.flush()
    company = _make_company(session, owner.id)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=owner.id,
            company_id=company.id,
            role="admin",
            is_primary=True,
            attached_at=datetime.now(timezone.utc),
        )
    )
    template = _make_template(owner.id)
    session.add(template)
    session.commit()

    backfill_from_primary_company(session.connection())
    session.commit()
    session.refresh(template)

    assert template.company_id == company.id


def test_backfill_from_sole_company_used_when_no_primary_flag(session):
    owner = _make_user(session, "tpl-owner-sole@test.com")
    session.flush()
    company = _make_company(session, owner.id)
    session.flush()
    session.add(
        UserCompanyAccessModel(
            user_id=owner.id,
            company_id=company.id,
            role="member",
            is_primary=False,
            attached_at=datetime.now(timezone.utc),
        )
    )
    template = _make_template(owner.id)
    session.add(template)
    session.commit()

    backfill_from_primary_company(session.connection())
    session.commit()
    session.refresh(template)
    assert template.company_id is None

    backfill_from_sole_company(session.connection())
    session.commit()
    session.refresh(template)
    assert template.company_id == company.id


def test_run_backfill_leaves_ambiguous_owner_null_and_reports_count(session):
    ambiguous_owner = _make_user(session, "tpl-ambiguous@test.com")
    no_company_owner = _make_user(session, "tpl-no-company@test.com")
    session.flush()
    company_a = _make_company(session, ambiguous_owner.id)
    company_b = _make_company(session, ambiguous_owner.id)
    session.flush()
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            UserCompanyAccessModel(
                user_id=ambiguous_owner.id, company_id=company_a.id, role="member", is_primary=False, attached_at=now
            ),
            UserCompanyAccessModel(
                user_id=ambiguous_owner.id, company_id=company_b.id, role="member", is_primary=False, attached_at=now
            ),
        ]
    )
    tpl_ambiguous = _make_template(ambiguous_owner.id, "Ambiguous Tpl")
    tpl_no_company = _make_template(no_company_owner.id, "No Company Tpl")
    session.add_all([tpl_ambiguous, tpl_no_company])
    session.commit()

    still_null = run_backfill(session.connection())
    session.commit()
    session.refresh(tpl_ambiguous)
    session.refresh(tpl_no_company)

    assert tpl_ambiguous.company_id is None
    assert tpl_no_company.company_id is None
    assert still_null == count_null_company_id(session.connection())
    assert still_null >= 2
