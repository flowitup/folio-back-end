"""Unit tests for GetCompanyProfileUseCase and UpsertCompanyProfileUseCase."""

import pytest

from app.application.billing.get_company_profile_usecase import GetCompanyProfileUseCase
from app.application.billing.upsert_company_profile_usecase import UpsertCompanyProfileUseCase
from app.application.billing.dtos import UpsertCompanyProfileInput
from tests.unit.application.billing.conftest import make_profile


@pytest.fixture
def get_uc(profile_repo):
    return GetCompanyProfileUseCase(profile_repo=profile_repo)


@pytest.fixture
def upsert_uc(profile_repo):
    return UpsertCompanyProfileUseCase(profile_repo=profile_repo)


def _upsert_inp(user_id, legal_name="Acme SAS", address="1 rue Test", **overrides):
    defaults = dict(user_id=user_id, legal_name=legal_name, address=address)
    defaults.update(overrides)
    return UpsertCompanyProfileInput(**defaults)


class TestGetCompanyProfile:
    def test_returns_none_when_not_set(self, get_uc, user_id):
        result = get_uc.execute(user_id)
        assert result is None

    def test_returns_profile_when_set(self, get_uc, profile_repo, user_id):
        p = make_profile(user_id)
        profile_repo.save(p)
        result = get_uc.execute(user_id)
        assert result is not None
        assert result.user_id == user_id

    def test_isolates_users(self, get_uc, profile_repo, user_id, other_user_id):
        p = make_profile(user_id)
        profile_repo.save(p)
        assert get_uc.execute(other_user_id) is None


class TestUpsertCompanyProfile:
    def test_creates_new_profile(self, upsert_uc, fake_session, user_id):
        inp = _upsert_inp(user_id)
        result = upsert_uc.execute(inp, fake_session)
        assert result.user_id == user_id
        assert result.legal_name == "Acme SAS"
        assert result.address == "1 rue Test"

    def test_updates_existing_profile(self, upsert_uc, profile_repo, fake_session, user_id):
        p = make_profile(user_id)
        profile_repo.save(p)
        inp = _upsert_inp(user_id, legal_name="New Name SAS")
        result = upsert_uc.execute(inp, fake_session)
        assert result.legal_name == "New Name SAS"

    def test_stores_optional_fields(self, upsert_uc, fake_session, user_id):
        inp = _upsert_inp(
            user_id,
            siret="12345678901234",
            tva_number="FR12345678901",
            iban="FR76123456789012345678901",
            prefix_override="FLW",
        )
        result = upsert_uc.execute(inp, fake_session)
        assert result.siret == "12345678901234"
        assert result.tva_number == "FR12345678901"
        assert result.prefix_override == "FLW"

    def test_empty_legal_name_raises(self, upsert_uc, fake_session, user_id):
        with pytest.raises(ValueError, match="legal name"):
            upsert_uc.execute(_upsert_inp(user_id, legal_name="  "), fake_session)

    def test_empty_address_raises(self, upsert_uc, fake_session, user_id):
        with pytest.raises(ValueError, match="address"):
            upsert_uc.execute(_upsert_inp(user_id, address=""), fake_session)

    def test_effective_prefix_empty_when_no_prefix(self, upsert_uc, fake_session, user_id):
        """Profile with no prefix_override → effective_prefix is empty string."""
        result = upsert_uc.execute(_upsert_inp(user_id), fake_session)
        # CompanyProfile.effective_prefix returns "" when prefix_override is None
        from app.domain.billing.company_profile import CompanyProfile
        from datetime import datetime, timezone

        profile = CompanyProfile(
            user_id=result.user_id,
            legal_name=result.legal_name,
            address=result.address,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            prefix_override=result.prefix_override,
        )
        assert profile.effective_prefix == ""
