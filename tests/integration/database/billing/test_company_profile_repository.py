"""Integration tests for SqlAlchemyCompanyProfileRepository against SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.domain.billing.company_profile import CompanyProfile
from app.infrastructure.database.models import UserModel
from app.infrastructure.database.repositories.sqlalchemy_company_profile_repository import (
    SqlAlchemyCompanyProfileRepository,
)


def _seed_user(session) -> UUID:
    user = UserModel(
        id=uuid4(),
        email=f"cp-{uuid4().hex[:8]}@test.com",
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    session.flush()
    return UUID(str(user.id))


def _make_profile(user_id: UUID, **overrides) -> CompanyProfile:
    now = datetime.now(timezone.utc)
    defaults = dict(
        user_id=user_id,
        legal_name="Acme SAS",
        address="1 rue de la Paix, 75001 Paris",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return CompanyProfile(**defaults)


class TestCompanyProfileRepository:
    def test_save_and_find_by_user_id(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyCompanyProfileRepository(session)
        profile = _make_profile(user_id)
        repo.save(profile)
        found = repo.find_by_user_id(user_id)
        assert found is not None
        assert found.legal_name == "Acme SAS"
        assert found.user_id == user_id

    def test_find_by_user_id_missing_returns_none(self, session):
        repo = SqlAlchemyCompanyProfileRepository(session)
        assert repo.find_by_user_id(uuid4()) is None

    def test_upsert_updates_existing(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyCompanyProfileRepository(session)
        profile = _make_profile(user_id, legal_name="Old Name")
        repo.save(profile)

        updated = profile.with_updates(legal_name="New Name")
        repo.save(updated)

        found = repo.find_by_user_id(user_id)
        assert found.legal_name == "New Name"

    def test_optional_fields_stored(self, session):
        user_id = _seed_user(session)
        repo = SqlAlchemyCompanyProfileRepository(session)
        profile = _make_profile(
            user_id,
            siret="12345678901234",
            tva_number="FR12345678901",
            iban="FR76123456789012345678901",
            bic="BNPAFRPPXXX",
            prefix_override="FLW",
            default_payment_terms="30 days net",
        )
        repo.save(profile)
        found = repo.find_by_user_id(user_id)
        assert found.siret == "12345678901234"
        assert found.tva_number == "FR12345678901"
        assert found.prefix_override == "FLW"
        assert found.default_payment_terms == "30 days net"

    def test_user_isolation(self, session):
        user_a = _seed_user(session)
        user_b = _seed_user(session)
        repo = SqlAlchemyCompanyProfileRepository(session)
        repo.save(_make_profile(user_a, legal_name="Company A"))
        repo.save(_make_profile(user_b, legal_name="Company B"))

        assert repo.find_by_user_id(user_a).legal_name == "Company A"
        assert repo.find_by_user_id(user_b).legal_name == "Company B"
