"""Phase 2 — labor_roles.company_id / slug backfill (shared with migration 2ca24be9e3a8).

Runs the exact statements the migration executes against the SQLite test
session fixture — see app.infrastructure.database.backfills.labor_roles_company.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.infrastructure.database.backfills.labor_roles_company import (
    SEED_THO_CHINH_ID,
    SEED_THO_CHINH_SLUG,
    SEED_THO_PHU_ID,
    SEED_THO_PHU_SLUG,
    backfill_company_when_sole,
    count_null_company_id,
    run_backfill,
    slug_seed_rows,
)
from app.infrastructure.database.models import LaborRoleModel, UserModel
from app.infrastructure.database.models.company import CompanyModel

PASSWORD_HASH = "x" * 60


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


def test_slug_seed_rows_sets_stable_slugs(session):
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            LaborRoleModel(id=UUID(SEED_THO_CHINH_ID), name="Thợ chính", color="#3B82F6", created_at=now),
            LaborRoleModel(id=UUID(SEED_THO_PHU_ID), name="Thợ phụ", color="#10B981", created_at=now),
        ]
    )
    session.commit()

    slug_seed_rows(session.connection())
    session.commit()

    chinh = session.get(LaborRoleModel, UUID(SEED_THO_CHINH_ID))
    phu = session.get(LaborRoleModel, UUID(SEED_THO_PHU_ID))
    assert chinh.slug == SEED_THO_CHINH_SLUG
    assert phu.slug == SEED_THO_PHU_SLUG


def test_slug_seed_rows_does_not_overwrite_existing_slug(session):
    now = datetime.now(timezone.utc)
    session.add(
        LaborRoleModel(id=UUID(SEED_THO_CHINH_ID), name="Thợ chính", color="#3B82F6", slug="custom", created_at=now)
    )
    session.commit()

    slug_seed_rows(session.connection())
    session.commit()

    assert session.get(LaborRoleModel, UUID(SEED_THO_CHINH_ID)).slug == "custom"


def test_backfill_company_when_sole_company_exists(session):
    owner = UserModel(id=uuid4(), email="lr-owner@test.com", password_hash=PASSWORD_HASH, is_active=True)
    session.add(owner)
    session.flush()
    company = _make_company(session, owner.id)
    session.flush()
    role = LaborRoleModel(id=uuid4(), name="Unscoped Role", color="#000000", created_at=datetime.now(timezone.utc))
    session.add(role)
    session.commit()

    backfill_company_when_sole(session.connection())
    session.commit()
    session.refresh(role)

    assert role.company_id == company.id


def test_backfill_company_when_sole_skips_when_multiple_companies(session):
    owner = UserModel(id=uuid4(), email="lr-owner2@test.com", password_hash=PASSWORD_HASH, is_active=True)
    session.add(owner)
    session.flush()
    _make_company(session, owner.id)
    _make_company(session, owner.id)
    session.flush()
    role = LaborRoleModel(id=uuid4(), name="Ambiguous Role", color="#000000", created_at=datetime.now(timezone.utc))
    session.add(role)
    session.commit()

    still_null = run_backfill(session.connection())
    session.commit()
    session.refresh(role)

    assert role.company_id is None
    assert still_null == count_null_company_id(session.connection())
    assert still_null >= 1
